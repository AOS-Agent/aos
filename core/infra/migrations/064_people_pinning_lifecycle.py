"""
Migration 064: Add pinned_importance and lifecycle_state to people table.

(Renumbered from 040 during release-train wave 4 promotion. Hardened
during transplant: the ALTER TABLE statements are now wrapped in
try/except with rollback, matching 060/061/062/067's pattern — previously
an unexpected SQLite error here would abort the rest of the migration
batch instead of failing just this one migration.)

pinned_importance: When set (1-4), the auto-classifier never overrides this
person's importance. Used for family members and operator-designated contacts
who should maintain a fixed importance regardless of communication volume.

lifecycle_state: Tracks the person's status beyond active/archived.
States: active (default), deceased, archived, merged, blocked.
Deceased people are never nudged for drift or reclassified.

Idempotent: re-running is safe.
"""

DESCRIPTION = "Add pinned_importance and lifecycle_state columns"

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
        return (
            _column_exists(conn, "people", "pinned_importance")
            and _column_exists(conn, "people", "lifecycle_state")
        )
    finally:
        conn.close()


def up() -> bool:
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not _column_exists(conn, "people", "pinned_importance"):
            conn.execute(
                "ALTER TABLE people ADD COLUMN pinned_importance INTEGER DEFAULT NULL"
            )
        if not _column_exists(conn, "people", "lifecycle_state"):
            conn.execute(
                "ALTER TABLE people ADD COLUMN lifecycle_state TEXT DEFAULT 'active'"
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 064 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
