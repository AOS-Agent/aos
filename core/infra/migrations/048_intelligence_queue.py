"""
Migration 048: intelligence_queue dedup index for People Intelligence nudges.

The `intelligence_queue` table itself was created by a legacy code path
(no migration of record, table is empty in operator DBs). This migration
ensures the table exists with the canonical schema and adds the missing
UNIQUE dedup index that lets the nudge generators rely on
``INSERT OR IGNORE`` for de-duplication.

Schema (canonical):

    intelligence_queue(
        id TEXT PRIMARY KEY,
        person_id TEXT REFERENCES people(id),
        surface_type TEXT NOT NULL,    -- birthday | drift | reconnect | follow_up
        priority INTEGER DEFAULT 3,
        surface_after INTEGER,         -- unix ts when nudge becomes live
        surfaced_at INTEGER,           -- when actually shown to operator
        status TEXT DEFAULT 'pending', -- pending | surfaced | dismissed | acted | expired
        content TEXT,                  -- operator-facing one-liner
        context_json TEXT,             -- JSON: tier, days_since, etc.
        created_at INTEGER,
        expires_at INTEGER
    );

    UNIQUE INDEX idx_queue_dedup ON intelligence_queue(person_id, surface_type, surface_after)

Idempotent: re-running is safe. Early-returns if people.db missing.
"""

DESCRIPTION = "intelligence_queue dedup index for People Intelligence nudges"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

DEDUP_INDEX_NAME = "idx_queue_dedup"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def check() -> bool:
    """Return True if migration has already been applied."""
    if not DB_PATH.exists():
        return True  # Fresh install
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not _table_exists(conn, "intelligence_queue"):
            return False
        return _index_exists(conn, DEDUP_INDEX_NAME)
    finally:
        conn.close()


def up() -> bool:
    """Create the table (if missing) and add the unique dedup index."""
    if not DB_PATH.exists():
        print(f"  people.db not found at {DB_PATH}, skipping")
        return True

    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Create the table if it doesn't already exist with the canonical
        # schema. The existing legacy table (if present) has the same
        # columns — we leave it untouched.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS intelligence_queue (
                id            TEXT PRIMARY KEY,
                person_id     TEXT REFERENCES people(id),
                surface_type  TEXT NOT NULL,
                priority      INTEGER DEFAULT 3,
                surface_after INTEGER,
                surfaced_at   INTEGER,
                status        TEXT DEFAULT 'pending',
                content       TEXT,
                context_json  TEXT,
                created_at    INTEGER,
                expires_at    INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_queue_pending
                ON intelligence_queue(status, surface_after, priority);

            CREATE INDEX IF NOT EXISTS idx_queue_person
                ON intelligence_queue(person_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_dedup
                ON intelligence_queue(person_id, surface_type, surface_after);
            """
        )
        conn.commit()
        print("  ✓ intelligence_queue dedup index ensured")
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 048 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
