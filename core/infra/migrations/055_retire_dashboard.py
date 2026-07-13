"""
Migration 055: Retire the old dashboard service.

The dashboard (com.aos.dashboard) has been fully replaced by Qareen
(com.aos.qareen) which runs on the same port 4096. This migration:

1. Stops and unloads com.aos.dashboard if it's still running
2. Removes the dashboard plist from ~/Library/LaunchAgents/
3. Creates the ingest tables in qareen.db (activity, conversations,
   sessions) used by the new /api/ingest endpoints

The old dashboard's data in ~/.aos/data/dashboard/activity.db is
left in place — it's historical and doesn't conflict.

Numbering note: this shipped as migration 030 on the unshipped
council-substrate branch (see core/infra/migrations/050_work_db_ownership.py
for the full renumbering rationale). Renumbered to 055 to land after main's
001-052 and after 053/054 (Qareen data model + service deploy).

Scope note: council-substrate later expanded this same migration number to
also retire eventd, companion, listen, mission-control, memory, and two
unused proxy services (commit 77b08c0). That expansion is out of scope for
the wave-2 "Qareen platform + dashboard retirement" transplant — this keeps
the original, narrower dashboard-only retirement. core/qareen/api/ingest.py
(the replacement endpoints referenced above) is transplanted separately as
part of the Qareen platform files, not through this migration.
"""

DESCRIPTION = "Retire old dashboard, create ingest tables in qareen.db"

import os
import sqlite3
import subprocess
from pathlib import Path

HOME = Path.home()
DASHBOARD_PLIST = HOME / "Library" / "LaunchAgents" / "com.aos.dashboard.plist"
DASHBOARD_LABEL = "com.aos.dashboard"
DB_PATH = HOME / ".aos" / "data" / "qareen.db"

INGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    parent_agent TEXT,
    status TEXT DEFAULT 'running',
    summary TEXT,
    duration_ms INTEGER,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_agent
    ON ingest_activity(agent);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_ts
    ON ingest_activity(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_session
    ON ingest_activity(session_id);

CREATE TABLE IF NOT EXISTS ingest_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',
    user_key TEXT NOT NULL,
    agent TEXT,
    topic_name TEXT,
    message TEXT NOT NULL,
    response TEXT,
    duration_ms INTEGER,
    message_type TEXT DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS idx_ingest_conv_ts
    ON ingest_conversations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_conv_agent
    ON ingest_conversations(agent);

CREATE TABLE IF NOT EXISTS ingest_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    working_dir TEXT,
    agent_name TEXT,
    status TEXT DEFAULT 'running',
    files_modified TEXT DEFAULT '[]',
    tools_used TEXT DEFAULT '{}',
    total_tools INTEGER DEFAULT 0,
    last_activity TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_sess_status
    ON ingest_sessions(status);
CREATE INDEX IF NOT EXISTS idx_ingest_sess_started
    ON ingest_sessions(started_at DESC);
"""


def check() -> bool:
    """Applied if dashboard plist is gone AND ingest tables exist."""
    if DASHBOARD_PLIST.exists():
        return False

    # Check if dashboard is still loaded in launchctl
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if DASHBOARD_LABEL in result.stdout:
            return False
    except Exception:
        pass

    # Check if ingest tables exist in qareen.db
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            conn.close()
            if "ingest_activity" not in tables:
                return False
        except Exception:
            pass

    return True


def up() -> bool:
    """Stop old dashboard, remove plist, create ingest tables."""
    actions = []
    uid = os.getuid()
    ingest_failed = False

    # 1. Stop the old dashboard if running (best-effort — a failure here
    # shouldn't block the migration; the plist removal below is what check()
    # actually verifies).
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if DASHBOARD_LABEL in result.stdout:
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{DASHBOARD_LABEL}"],
                capture_output=True, timeout=10,
            )
            actions.append(f"Stopped {DASHBOARD_LABEL}")
    except Exception as e:
        actions.append(f"Could not stop dashboard: {e}")

    # 2. Remove the plist
    if DASHBOARD_PLIST.exists():
        DASHBOARD_PLIST.unlink()
        actions.append(f"Removed {DASHBOARD_PLIST}")

    # 3. Create ingest tables in qareen.db — this is the migration's actual
    # payload (core/qareen/api/ingest.py depends on these tables existing),
    # so unlike the best-effort steps above, a failure here fails the
    # migration outright rather than being swallowed into the log line.
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(INGEST_SCHEMA)
            conn.commit()
            conn.close()
            actions.append("Created ingest tables in qareen.db")
        except Exception as e:
            actions.append(f"Ingest table creation failed: {e}")
            ingest_failed = True

    logger_msg = "; ".join(actions) if actions else "Nothing to do"
    print(f"  {logger_msg}")

    if ingest_failed:
        return False
    return True
