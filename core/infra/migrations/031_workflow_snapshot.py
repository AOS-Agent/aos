"""
Migration 031: Add workflow_snapshot column to automations table.

Stores the last-saved workflow JSON so users can restore a previous
version without needing n8n state. Snapshot is written on every save.
"""

DESCRIPTION = "Add workflow_snapshot column to automations table"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"


def check() -> bool:
    """Applied if the workflow_snapshot column exists."""
    if not DB_PATH.exists():
        return True  # No DB yet — nothing to migrate
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(automations)").fetchall()}
        conn.close()
        return "workflow_snapshot" in cols
    except Exception:
        return False


def apply() -> str:
    """Add workflow_snapshot TEXT column to automations table."""
    if not DB_PATH.exists():
        return "No qareen.db — skipped"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("ALTER TABLE automations ADD COLUMN workflow_snapshot TEXT")
        conn.commit()
        conn.close()
        return "Added workflow_snapshot column to automations"
    except Exception as e:
        return f"Failed: {e}"
