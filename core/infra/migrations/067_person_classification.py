"""
Migration 067: person_classification + classification_feedback tables.

(Renumbered from 034 during release-train wave 4 promotion — original
numbering collided with unrelated wave-6/7 work by the time it was
promoted, so council-substrate itself had already renamed this file to
047 before the wave-4 dossier's own analysis; this transplant renumbers
directly from the original 034 content to main's next free slot, 067.
Docstring during transplant: stripped a dead reference to a local
Claude-Code plan-mode scratch file that has no meaning outside that
session's history.)

Adds the persistence layer for Phase 4 of the People Intelligence
subsystem.

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

Idempotent: re-running is safe. If people.db doesn't exist yet, up() and
check() lazily create it via the framework's own connect() rather than
skipping — see 060_people_ontology.py's _ensure_people_db() docstring
(aos#153).
"""

DESCRIPTION = (
    "person_classification + classification_feedback tables for Phase 4"
)

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
        return _table_exists(conn, "person_classification") and _table_exists(
            conn, "classification_feedback"
        )
    finally:
        conn.close()


def up() -> bool:
    """Create the classification tables and supporting indexes."""
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

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
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 067 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
