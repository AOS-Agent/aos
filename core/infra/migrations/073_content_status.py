"""
Migration 033: Add content_status column to intelligence_briefs.

Part of the deferred-extraction redesign. Ingest now stores RSS metadata
only and sets content_status='pending'. Full content is fetched on demand
via POST /api/intelligence/items/{id}/extract.

Values:
    pending     — RSS metadata only, no full content yet
    extracting  — extraction in flight
    extracted   — full content present
    failed      — extraction attempted and failed (will not retry automatically)
"""

DESCRIPTION = "Add content_status column to intelligence_briefs"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == col for row in info)
    except sqlite3.OperationalError:
        return False


def check() -> bool:
    """Applied if content_status column already exists."""
    if not DB_PATH.exists():
        return True  # no DB yet, schema will include the column
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_briefs'"
        ).fetchone():
            return True  # table doesn't exist, nothing to do
        return _has_column(conn, "intelligence_briefs", "content_status")
    finally:
        conn.close()


def up() -> bool:
    """Add content_status column with default 'pending'."""
    if not DB_PATH.exists():
        return True  # No DB yet; schema creation covers a fresh install.

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_briefs'"
        ).fetchone():
            return True  # Table not created yet; schema will include the column.

        if _has_column(conn, "intelligence_briefs", "content_status"):
            return True

        conn.execute(
            "ALTER TABLE intelligence_briefs "
            "ADD COLUMN content_status TEXT DEFAULT 'pending'"
        )

        # Backfill: rows that already have content become 'extracted',
        # everything else stays 'pending'.
        conn.execute(
            "UPDATE intelligence_briefs "
            "SET content_status = 'extracted' "
            "WHERE content IS NOT NULL AND LENGTH(content) > 0"
        )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_briefs_content_status "
            "ON intelligence_briefs(content_status)"
        )

        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
