"""
Migration 058: Add workflow_snapshot column to automations table.

(Renumbered from 031 during release-train wave 3 promotion; see 056's
docstring for why the renumber is load-bearing, not cosmetic. Also
converted from the legacy apply()-returns-string convention to
up()-returns-bool — see aos#149 / runner.py's strict True/None-only
success contract.)

Stores the last-saved workflow JSON so users can restore a previous
version without needing n8n state. Snapshot is written on every save.

Soft dependency: this ALTER TABLE only succeeds once the `automations`
table has been created, which happens lazily the first time the
Automations API (migration 056/n8n) is actually exercised, not merely
once 056 has run. check() treats "table doesn't exist yet" as
not-yet-applicable and up() no-ops safely in that case (see below).
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
