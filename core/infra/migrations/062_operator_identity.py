"""
Migration 062: operator self-identity in people.db.

(Renumbered from 038 during release-train wave 4 promotion. Wave-plan
note corrected during transplant: this migration has no onboarding-state
gate — its only precondition is people.db existing, same as the other 8
migrations in this wave. See _ensure_people_db().)

Adds a `people.is_self` column to mark the operator's own person row.
This is what lets every other table answer "who is the operator?" with
a single SQL query — currently the answer lives only in
~/.aos/config/operator.yaml as a name string with no FK.

Schema change is one boolean column (default 0). This migration does
NOT pick which row is the operator — that's an instance-specific data
fix done by `core/bin/internal/operator-link` (separate tool).

Idempotent: re-running is safe.
"""

DESCRIPTION = "Add people.is_self column for operator identity"

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
        return _column_exists(conn, "people", "is_self")
    finally:
        conn.close()


def up() -> bool:
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not _column_exists(conn, "people", "is_self"):
            conn.execute("ALTER TABLE people ADD COLUMN is_self INTEGER DEFAULT 0")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_people_is_self ON people(is_self) WHERE is_self = 1"
            )
            conn.commit()
            print("  ✓ people.is_self column added")
        else:
            print("  people.is_self already present")
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 062 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
