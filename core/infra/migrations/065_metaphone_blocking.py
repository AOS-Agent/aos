"""
Migration 065: Add metaphone_key column for phonetic dedup blocking.

(Renumbered from 041 during release-train wave 4 promotion. Hardened
during transplant: the column/index creation is now wrapped in
try/except with rollback — previously only the metaphone backfill's
ImportError was caught; an unexpected SQLite error in the ALTER/CREATE
statements would have aborted the rest of the migration batch.)

Double Metaphone produces phonetic codes that handle Western name
variations (Catherine/Katherine, Steven/Stephen) and complement the
existing Arabic phonetic groups in normalize.py. Used as a blocking
key in the dedup engine to reduce O(n^2) comparisons.

Idempotent: re-running is safe.
"""

DESCRIPTION = "Add metaphone_key column to people table"

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"


def _ensure_people_db() -> bool:
    """Ensure people.db exists, creating it via the framework's own
    db.connect() if it doesn't. See 060_people_ontology.py's copy of this
    helper for the full rationale (aos#153)."""
    # No file-exists early return: a partial/schema-less people.db (from an
    # interrupted run or a bare service touch) must still get the schema.
    # people_db.connect() gates on the 'people' TABLE and schema.sql is
    # fully idempotent (IF NOT EXISTS), so this is always safe. Clean-box
    # finding 2026-07-15 — same file-exists-vs-schema-exists class as
    # 053/058.
    core_dir = next((p for p in Path(__file__).resolve().parents if p.name == "core"), None)
    people_dir = core_dir / "engine" / "people" if core_dir else None
    if not people_dir or not people_dir.exists():
        return False
    if str(people_dir) not in sys.path:
        sys.path.insert(0, str(people_dir))
    try:
        import db as people_db
        people_db.connect().close()
    except Exception as e:
        print(f"  Could not lazily create people.db: {e}")
        return False
    return True


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def check() -> bool:
    if not _ensure_people_db():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return _column_exists(conn, "people", "metaphone_key")
    finally:
        conn.close()


def up() -> bool:
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not _column_exists(conn, "people", "metaphone_key"):
            conn.execute("ALTER TABLE people ADD COLUMN metaphone_key TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_people_metaphone ON people(metaphone_key)")
            conn.commit()
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        conn.rollback()
        conn.close()
        return False

    # Backfill metaphone keys (best-effort — missing `metaphone` package
    # just delays the backfill to next cycle, doesn't fail the migration)
    try:
        try:
            from metaphone import doublemetaphone

            rows = conn.execute(
                "SELECT id, canonical_name FROM people WHERE metaphone_key IS NULL AND canonical_name IS NOT NULL"
            ).fetchall()
            for row in rows:
                words = (row[1] or "").lower().split()
                codes = []
                for w in words:
                    primary, _ = doublemetaphone(w)
                    codes.append(primary if primary else w)
                key = " ".join(codes)
                conn.execute(
                    "UPDATE people SET metaphone_key = ? WHERE id = ?",
                    (key, row[0]),
                )
            conn.commit()
            print(f"  Backfilled {len(rows)} metaphone keys")
        except ImportError:
            print("  metaphone not installed — skipping backfill (will run on next cycle)")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 065 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
