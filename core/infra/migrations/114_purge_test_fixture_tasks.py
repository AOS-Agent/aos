"""
Migration 114: delete test-fixture tasks that leaked into the live work.db.

For roughly three and a half months the test suite wrote to the operator's real
database. `tests/test_engine.py` uses six literal titles; the live work.db holds
368 rows carrying exactly those strings, dated 2026-04-02 → 2026-07-15, with a
single day (2026-07-14) accounting for 470 task creations of which 444 landed
project-less in `t#` (work-engine audit 2026-08-20 §4). Test isolation via a
`tmp_path` fixture landed afterwards and stopped the bleeding; nothing ever
cleaned up what had already leaked.

These rows are 17% of the task table and the bulk of the `t#` bucket the
operator sees in every session-start briefing.

**Three conditions, all required** — this is the guard that makes the deletion
safe rather than merely likely-safe:

  1. `title` is an exact match (`=`, never LIKE) for one of the six fixture
     strings;
  2. `created_at` falls inside the leak window (2026-04-01 → 2026-07-16);
  3. the task has **zero rows in `task_activity`** — nobody ever started,
     touched, or commented on it.

A real task someone genuinely named "Fix the login bug" survives all three
tests unless it was created inside the window AND never touched once. Rows are
counted per title and the counts printed before deletion, so the log says what
went, not just that something did.

Backup-first: work.db is copied to ~/.aos/backups/pre-purge/ before any DELETE.
Subtasks are re-parented rather than orphaned — a fixture task with a real
child would otherwise leave a row pointing at a parent that no longer exists —
though in practice the fixture rows have no children.

Idempotent: check() passes once zero rows match all three conditions.
"""

from __future__ import annotations

DESCRIPTION = "Purge 368 test-fixture tasks that leaked into live work.db"

import shutil
import sqlite3
import time
from pathlib import Path

HOME = Path.home()
WORK_DB = HOME / ".aos" / "data" / "work.db"
BACKUP_DIR = HOME / ".aos" / "backups" / "pre-purge"

# The exact literals in tests/test_engine.py. Exact match only.
FIXTURE_TITLES = (
    "Fix the login bug",
    "Deploy the bridge service",
    "Refactor the database connection pool",
    "Real task",
    "First task",
    "Second task",
)

# The leak window, one day either side of the observed first/last row.
WINDOW_START = "2026-04-01"
WINDOW_END = "2026-07-16"

_PLACEHOLDERS = ",".join("?" for _ in FIXTURE_TITLES)

# All three conditions in one predicate, used by count, list and delete alike so
# they can never drift apart.
_MATCH = f"""
    title IN ({_PLACEHOLDERS})
    AND date(created_at) BETWEEN ? AND ?
    AND NOT EXISTS (
        SELECT 1 FROM task_activity a WHERE a.task_id = tasks.id
    )
"""
_PARAMS = (*FIXTURE_TITLES, WINDOW_START, WINDOW_END)


def _connect(readonly: bool = False) -> sqlite3.Connection | None:
    if not WORK_DB.exists():
        return None
    try:
        if readonly:
            return sqlite3.connect(f"file:{WORK_DB}?mode=ro", uri=True)
        return sqlite3.connect(str(WORK_DB))
    except sqlite3.Error:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _matching(conn: sqlite3.Connection) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE {_MATCH}", _PARAMS
    ).fetchone()[0]


def check() -> bool:
    """Applied when no row matches all three conditions."""
    conn = _connect(readonly=True)
    if conn is None:
        return True  # no work.db on this machine — nothing to purge
    try:
        if not _table_exists(conn, "tasks") or not _table_exists(conn, "task_activity"):
            return True
        return _matching(conn) == 0
    except sqlite3.Error:
        return True
    finally:
        conn.close()


def up() -> bool:
    conn = _connect()
    if conn is None:
        print("  No work.db on this machine — nothing to purge")
        return True

    try:
        if not _table_exists(conn, "tasks") or not _table_exists(conn, "task_activity"):
            print("  work.db has no tasks/task_activity tables — nothing to purge")
            return True

        total = _matching(conn)
        if total == 0:
            print("  No fixture rows match all three conditions — nothing to purge")
            return True

        print(f"  Matched {total} fixture task(s) in {WINDOW_START}..{WINDOW_END}:")
        for title in FIXTURE_TITLES:
            n = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE title = ? AND {_MATCH}",
                (title, *_PARAMS),
            ).fetchone()[0]
            if n:
                print(f"    {n:>4}  {title!r}")
    finally:
        conn.close()

    # Backup before the first write.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"work.db.bak-{stamp}"
    try:
        shutil.copy2(WORK_DB, dest)
        print(f"  ✓ Backed up work.db → {dest}")
    except OSError as e:
        print(f"  ✗ Could not back up work.db ({e}) — refusing to delete")
        return False

    conn = _connect()
    if conn is None:
        return False
    try:
        # Re-parent any child of a doomed row to that row's own parent, so no
        # subtask is left pointing at a task that no longer exists.
        reparented = 0
        if conn.execute("SELECT 1 FROM pragma_table_info('tasks') WHERE name='parent_id'").fetchone():
            cur = conn.execute(
                f"""
                UPDATE tasks SET parent_id = (
                    SELECT p.parent_id FROM tasks p WHERE p.id = tasks.parent_id
                )
                WHERE parent_id IN (SELECT id FROM tasks WHERE {_MATCH})
                """,
                _PARAMS,
            )
            reparented = cur.rowcount or 0

        cur = conn.execute(f"DELETE FROM tasks WHERE {_MATCH}", _PARAMS)
        deleted = cur.rowcount or 0
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        conn.close()
        print(f"  ✗ Purge failed ({e}) — rolled back; backup at {dest}")
        return False
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    if reparented:
        print(f"  ✓ Re-parented {reparented} subtask(s) around deleted rows")
    print(f"  ✓ Deleted {deleted} fixture task(s); work.db vacuumed")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 114 already applied" if check() else ("Done" if up() else "Failed"))
