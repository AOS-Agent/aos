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


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='automations'"
    ).fetchone()
    return row is not None


def check() -> bool:
    """Applied if the workflow_snapshot column exists.

    The `automations` table itself is created lazily by the Automations
    API (migration 056/n8n), not by any numbered migration — so "table
    doesn't exist yet" is a legitimate not-yet-applicable state, exactly
    like "no DB yet", and must short-circuit here rather than reach
    up()'s ALTER TABLE with a nonexistent table (which previously threw
    sqlite3.OperationalError, got swallowed into a string, and was
    recorded as success forever — see docstring above).
    """
    if not DB_PATH.exists():
        return True  # No DB yet — nothing to migrate
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            if not _table_exists(conn):
                return True  # automations table not created yet — nothing to migrate
            cols = {row[1] for row in conn.execute("PRAGMA table_info(automations)").fetchall()}
            return "workflow_snapshot" in cols
        finally:
            conn.close()
    except Exception:
        return False


def up() -> bool:
    """Add workflow_snapshot TEXT column to automations table.

    Only reachable when check() has already confirmed the automations
    table exists and lacks the column — so the ALTER TABLE below should
    always succeed for a genuine schema change. True on success or
    nothing-to-do, False (never a string) on a real failure, per
    runner.py's strict True/None-only success contract.
    """
    if not DB_PATH.exists():
        return True  # No DB yet — nothing to do (mirrors check())
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            if not _table_exists(conn):
                print("automations table not created yet — skipping (nothing to migrate)")
                return True
            conn.execute("ALTER TABLE automations ADD COLUMN workflow_snapshot TEXT")
            conn.commit()
            print("Added workflow_snapshot column to automations")
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"Failed to add workflow_snapshot column: {e}")
        return False
