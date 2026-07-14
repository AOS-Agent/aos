"""
Migration 061: signal_store table for People Intelligence.

(Renumbered from 032 during release-train wave 4 promotion.)

Adds the `signal_store` table to people.db — the persistence layer for
extracted signals from the People Intelligence subsystem
(core/engine/people/intel/). Each row stores JSON-serialized signals for
one person × one source, so re-running a single adapter only overwrites
its own row.

Idempotent: re-running is safe. If people.db doesn't exist yet, up() and
check() lazily create it via the framework's own connect() rather than
skipping — see _ensure_people_db()'s docstring (aos#153).
"""

DESCRIPTION = "signal_store table for People Intelligence signal persistence"

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"


def _ensure_people_db() -> bool:
    """Ensure people.db exists, creating it via the framework's own
    db.connect() if it doesn't. See 060_people_ontology.py's copy of this
    helper for the full rationale (aos#153) — every wave-4 migration except
    039 carries an identical copy since migrations are loaded standalone by
    runner.py (no shared imports between them)."""
    if DB_PATH.exists():
        return True
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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def check() -> bool:
    """Return True if migration has already been applied."""
    if not _ensure_people_db():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return _table_exists(conn, "signal_store")
    finally:
        conn.close()


def up() -> bool:
    """Create the signal_store table and supporting indexes."""
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signal_store (
                person_id    TEXT NOT NULL,
                source_name  TEXT NOT NULL,
                signals_json TEXT NOT NULL,
                extracted_at INTEGER NOT NULL,
                PRIMARY KEY (person_id, source_name)
            );

            CREATE INDEX IF NOT EXISTS idx_signal_store_person
                ON signal_store(person_id);

            CREATE INDEX IF NOT EXISTS idx_signal_store_source
                ON signal_store(source_name);

            CREATE INDEX IF NOT EXISTS idx_signal_store_extracted_at
                ON signal_store(extracted_at);
            """
        )
        conn.commit()
        print("  ✓ signal_store table created")
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 061 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
