"""
Migration 047: person_classification + classification_feedback tables.

Adds the persistence layer for Phase 4 of the People Intelligence
subsystem. See docs/plans/2026-04-06-people-intelligence-v2.md and
~/.claude/plans/glowing-swinging-stroustrup.md for the full design.

Two tables:

1. person_classification
   - One active row per person (PRIMARY KEY = person_id)
   - Stores the latest ClassificationResult: tier + context tags JSON
   - `tier` indexed for fast tier-distribution queries
   - `run_id` indexed for per-run audit queries

2. classification_feedback
   - Append-only log of operator corrections
   - Each row captures old/new tier + tags + free-text notes
   - Fed back into future LLM classifier runs as few-shot examples
   - Indexed by person_id and created_at for recent-first queries

Idempotent: re-running is safe. Early-returns if people.db does not
exist yet (fresh install) — the tables will be created on the next run
once people.db is initialized by the ontology migration.
"""

DESCRIPTION = (
    "person_classification + classification_feedback tables for Phase 4"
)

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"


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
    if not DB_PATH.exists():
        return True  # Fresh install — nothing to migrate
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return _table_exists(conn, "person_classification") and _table_exists(
            conn, "classification_feedback"
        )
    finally:
        conn.close()


def up() -> bool:
    """Create the classification tables and supporting indexes."""
    if not DB_PATH.exists():
        print(f"  people.db not found at {DB_PATH}, skipping")
        return True

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS person_classification (
                person_id         TEXT NOT NULL,
                tier              TEXT NOT NULL,
                context_tags_json TEXT NOT NULL DEFAULT '[]',
                reasoning         TEXT,
                model             TEXT,
                run_id            TEXT NOT NULL,
                created_at        INTEGER NOT NULL,
                PRIMARY KEY (person_id)
            );

            CREATE INDEX IF NOT EXISTS idx_person_classification_tier
                ON person_classification(tier);

            CREATE INDEX IF NOT EXISTS idx_person_classification_run
                ON person_classification(run_id);

            CREATE INDEX IF NOT EXISTS idx_person_classification_created
                ON person_classification(created_at);

            CREATE TABLE IF NOT EXISTS classification_feedback (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id     TEXT NOT NULL,
                old_tier      TEXT,
                old_tags_json TEXT,
                new_tier      TEXT,
                new_tags_json TEXT,
                notes         TEXT,
                created_at    INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_classification_feedback_person
                ON classification_feedback(person_id);

            CREATE INDEX IF NOT EXISTS idx_classification_feedback_created
                ON classification_feedback(created_at);
            """
        )
        conn.commit()
        print("  ✓ person_classification + classification_feedback tables created")
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 047 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
