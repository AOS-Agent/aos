"""
Migration 050: Work DB ownership — copy the work tables into a kernel-owned
``~/.aos/data/work.db``.

Phase 1 of the work/task storage collapse (aos#130). The locked architecture
decision is that the kernel owns work/task state and Qareen reads it via the
kernel. Today the kernel work engine (core/engine/work/backend.py) opens
Qareen's own database (``~/.aos/data/qareen.db``) directly. This migration
introduces the new kernel-owned location and seeds it from qareen.db so the
backend can cut over to it.

Numbering note: migrations 023-049 live on the unshipped council-substrate
branch and were applied to this instance out-of-band from the dev workspace
(the live ~/.aos/.version is still 22 while qareen.db already carries their
schema). Numbering continues from 050 to avoid a collision when that branch
eventually ships. The runner runs migrations with number > current version and
globs whatever files are present, so on the shipped main line (which has files
001-022 + 050) it simply runs 050; the 023-049 gap is intentional and safe.

What it does:
  - Copies the work-related tables from qareen.db into work.db using an
    ATTACH + ``INSERT ... SELECT`` — schema is read from the source at
    runtime (via sqlite_master) so it never drifts from the live columns,
    which carry migration-added fields the shipped qareen.sql does not.
  - Rebuilds the ``tasks_fts`` FTS5 index from the copied ``tasks`` content
    (it is an external-content table: ``content=tasks``).
  - Copies the indexes on the work tables so the new store performs like the
    old one.

What it deliberately does NOT do:
  - It does not delete anything from qareen.db. Qareen keeps reading its own
    copy until its API is repointed at the kernel (a separate task). Running
    both side by side is intentional for this phase.
  - It does not touch the shared ``links`` table. Work-object relationships
    stay in qareen.db for now; ``links`` also holds non-work relationships
    (people, briefs), so moving it needs its own decision when Qareen is
    repointed.

Resolution/injection:
  - Source defaults to ~/.aos/data/qareen.db, overridable via AOS_WORK_DB_SRC.
  - Destination defaults to ~/.aos/data/work.db, overridable via AOS_WORK_DB
    (the same env var the backend honors), which makes the migration testable
    against temp copies without touching the live instance.

Idempotent: ``check()`` reports applied once work.db has the ``tasks`` table,
so it is a safe no-op on machines that already have a seeded work.db. ``up()``
also skips if qareen.db is absent (fresh machine — the backend falls back) or
if work.db already carries the work tables, and copies within a single
transaction so a partial failure leaves work.db clean for a re-run.
"""

DESCRIPTION = "Work DB ownership: seed kernel-owned work.db from qareen.db"

import os
import sqlite3
from pathlib import Path

HOME = Path.home()

# Work tables to copy, in foreign-key dependency order (parents first) so the
# copy is clean even with foreign_keys enforcement on. tasks_fts is handled
# separately (it is a virtual table, rebuilt from content).
WORK_TABLES = [
    "projects",
    "areas",
    "goals",
    "key_results",
    "workflows",
    "workflow_runs",
    "threads",
    "inbox",
    "tasks",
    "task_handoffs",
]

FTS_TABLE = "tasks_fts"


def _src_db() -> Path:
    """Source DB — Qareen's database, or an override for testing."""
    override = os.environ.get("AOS_WORK_DB_SRC")
    if override:
        return Path(override).expanduser()
    return HOME / ".aos" / "data" / "qareen.db"


def _dst_db() -> Path:
    """Destination DB — the kernel-owned work store (AOS_WORK_DB override wins)."""
    override = os.environ.get("AOS_WORK_DB")
    if override:
        return Path(override).expanduser()
    return HOME / ".aos" / "data" / "work.db"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def check() -> bool:
    """Applied once the destination work.db exists and carries the tasks table."""
    dst = _dst_db()
    if not dst.exists():
        return False
    conn = sqlite3.connect(str(dst))
    try:
        return _table_exists(conn, "tasks")
    finally:
        conn.close()


def up() -> bool:
    """Seed work.db from qareen.db. Safe to re-run; skips when nothing to do."""
    src = _src_db()
    dst = _dst_db()

    if not src.exists():
        # Fresh machine with no Qareen DB yet — nothing to seed. The backend
        # falls back to qareen.db until a real DB exists, so this is a no-op.
        print(f"  → source {src} does not exist yet; skipping (backend falls back)")
        return True

    # Autocommit mode: ATTACH cannot run inside a transaction, and we manage
    # the copy transaction explicitly so a partial failure rolls back cleanly.
    dst.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dst))
    conn.isolation_level = None
    try:
        if _table_exists(conn, "tasks"):
            print("  → work.db already has the work tables; nothing to do")
            return True

        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ATTACH DATABASE ? AS src", (str(src),))

        # What does the source actually have?
        src_objects = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT name, sql FROM src.sqlite_master WHERE type='table'"
            ).fetchall()
        }

        counts: dict[str, int] = {}
        conn.execute("BEGIN")
        try:
            for table in WORK_TABLES:
                create_sql = src_objects.get(table)
                if not create_sql:
                    # Table not present in this source (e.g. never provisioned).
                    continue
                if not _table_exists(conn, table):
                    conn.execute(create_sql)
                conn.execute(
                    f'INSERT INTO "{table}" SELECT * FROM src."{table}"'
                )
                counts[table] = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]

            # Indexes on the copied work tables (skip auto-indexes: sql IS NULL).
            for name, tbl, idx_sql in conn.execute(
                "SELECT name, tbl_name, sql FROM src.sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL"
            ).fetchall():
                if tbl not in WORK_TABLES:
                    continue
                try:
                    conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass  # index already present / non-fatal

            # Preserve AUTOINCREMENT counters for copied tables (key_results).
            if _table_exists(conn, "sqlite_sequence"):
                try:
                    for seq_name, seq_val in conn.execute(
                        "SELECT name, seq FROM src.sqlite_sequence"
                    ).fetchall():
                        if seq_name in WORK_TABLES:
                            conn.execute(
                                "INSERT OR REPLACE INTO sqlite_sequence(name, seq) "
                                "VALUES (?, ?)",
                                (seq_name, seq_val),
                            )
                except sqlite3.OperationalError:
                    pass

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        # Rebuild the FTS index outside the copy transaction. It is an
        # external-content table (content=tasks); 'rebuild' repopulates the
        # shadow tables from the copied tasks rows. Non-fatal: the backend
        # keeps tasks_fts in sync on writes via _sync_fts.
        if FTS_TABLE in src_objects and _table_exists(conn, "tasks"):
            try:
                if not _table_exists(conn, FTS_TABLE):
                    conn.execute(src_objects[FTS_TABLE])
                conn.execute(
                    f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')"
                )
                print(f"  ✓ rebuilt {FTS_TABLE} from copied tasks")
            except sqlite3.OperationalError as e:
                print(f"  ⚠ {FTS_TABLE} rebuild skipped: {e}")

        conn.execute("DETACH DATABASE src")

        summary = ", ".join(f"{t}={counts[t]}" for t in WORK_TABLES if t in counts)
        print(f"  ✓ seeded {dst} ({summary})")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 050 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
