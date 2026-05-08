"""044 — Style Intelligence tables."""

import sqlite3
from pathlib import Path

DESCRIPTION = "Add style_profiles and style_modes tables for per-relationship communication style"

PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"


def check() -> bool:
    """Return True if migration already applied."""
    if not PEOPLE_DB.exists():
        return False
    conn = sqlite3.connect(str(PEOPLE_DB))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        return "style_profiles" in tables and "style_modes" in tables
    finally:
        conn.close()


def up() -> None:
    """Create style intelligence tables."""
    if not PEOPLE_DB.exists():
        return
    conn = sqlite3.connect(str(PEOPLE_DB))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS style_profiles (
                person_id         TEXT PRIMARY KEY REFERENCES people(id),
                computed_at       INTEGER NOT NULL,
                sample_size       INTEGER NOT NULL,
                recompute_after   INTEGER NOT NULL,
                language_mix      TEXT,
                romanized_ar      INTEGER DEFAULT 0,
                signs_off         INTEGER DEFAULT 0,
                uses_periods      INTEGER DEFAULT 0,
                response_style    TEXT,
                enhancement_default TEXT DEFAULT 'clean',
                prose_summary     TEXT,
                style_markers     TEXT
            );

            CREATE TABLE IF NOT EXISTS style_modes (
                person_id          TEXT NOT NULL REFERENCES people(id),
                mode_name          TEXT NOT NULL,
                weight             REAL NOT NULL,
                signature          TEXT NOT NULL,
                exemplar_ids       TEXT NOT NULL,
                topic_correlations TEXT,
                PRIMARY KEY (person_id, mode_name)
            );

            CREATE INDEX IF NOT EXISTS idx_style_profiles_recompute
                ON style_profiles(recompute_after);

            CREATE INDEX IF NOT EXISTS idx_style_modes_person
                ON style_modes(person_id);
        """)
        conn.commit()
    finally:
        conn.close()
