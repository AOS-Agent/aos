"""
Migration 070: Sentinel autonomous commitment agent — infrastructure init.

(069 intentionally unallocated: council-substrate's 044_style_intelligence was
superseded by wave 4, whose people schema.sql already ships style_profiles and
style_modes verbatim — see schema.sql:254-281. The empty delta was dropped
rather than land a comms migration re-declaring people-owned tables.)

Creates the schema, runtime directories, and config file for Sentinel —
the agent that handles operator commitments made in iMessage via trigger
phrases ('consider it done', '@aos').

Creates:
1. `agent_triggers` table in comms.db
2. ~/.aos/work/sentinel/{drafts,pending}/
3. ~/.aos/logs/sentinel/
4. ~/.aos/config/sentinel.yaml (with sane defaults)

Idempotent: safe to re-run. Checks before touching anything.

aos#153 note: an earlier draft of this migration skipped schema creation when
comms.db did not exist yet and dropped a `.schema_pending` marker for a
consumer to pick up later — but nothing ever read that marker, so on a machine
where this migration ran before comms.db was first created, `agent_triggers`
was permanently stranded (the runner's watermark is a single monotonic integer;
once past this migration it is never reconsidered). agent_triggers is
Sentinel-owned and self-contained (no foreign keys into the rest of comms.db),
so this migration now creates comms.db itself (sqlite3.connect creates the file)
and applies the table directly. comms-bus's own tables are created with
CREATE TABLE IF NOT EXISTS on first run, so an early-created file is harmless.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DESCRIPTION = "Sentinel agent infrastructure (table, dirs, config)"

HOME = Path.home()
DATA_DIR = HOME / ".aos" / "data"
COMMS_DB = DATA_DIR / "comms.db"
WORK_DIR = HOME / ".aos" / "work" / "sentinel"
LOG_DIR = HOME / ".aos" / "logs" / "sentinel"
CONFIG_PATH = HOME / ".aos" / "config" / "sentinel.yaml"

DIRS = [
    WORK_DIR / "drafts",
    WORK_DIR / "pending",
    LOG_DIR,
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_triggers (
    id                  TEXT PRIMARY KEY,
    message_id          TEXT UNIQUE NOT NULL,
    person_id           TEXT,
    channel             TEXT NOT NULL,
    trigger_phrase      TEXT NOT NULL,
    agent_name          TEXT NOT NULL,
    status              TEXT NOT NULL,
    task_inferred       TEXT,
    confidence          TEXT,
    confidence_reasons  TEXT,
    draft_path          TEXT,
    error               TEXT,
    created_at          INTEGER NOT NULL,
    spawned_at          INTEGER,
    draft_at            INTEGER,
    decided_at          INTEGER,
    sent_at             INTEGER
);
CREATE INDEX IF NOT EXISTS idx_triggers_status ON agent_triggers(status);
CREATE INDEX IF NOT EXISTS idx_triggers_person ON agent_triggers(person_id);
CREATE INDEX IF NOT EXISTS idx_triggers_created ON agent_triggers(created_at);
"""

DEFAULT_CONFIG = """# Sentinel configuration
# Autonomous commitment agent — wakes on trigger phrases in outbound iMessage.

enabled: true
paused: false

# Trigger phrases — word-boundary regex, case-insensitive
trigger_phrases:
  - "consider it done"
  - "@aos"

# Channels Sentinel listens on
channels:
  - imessage

# Auto-send confidence requirements (ALL must be true for high)
confidence_criteria:
  require_pure_research: true        # no side-effect tasks
  min_sources: 2                     # corroborating sources
  require_style_match: true          # match operator voice
  block_inner_circle: true           # importance == 1 always blocks
  block_unverified_facts: true       # no unsourced numbers/dates
  rate_limit_hours: 1                # max 1 send per contact per N hours

# Hard floor blocks (always prevent send, even at high confidence)
hard_floor:
  blocked_intent_words:
    - book
    - schedule
    - pay
    - buy
    - "send money"
  block_inner_circle_importance: 1

# Soft window — seconds between draft_ready and send
soft_window_seconds: 30

# Subprocess limits
spawn_timeout_seconds: 300           # 5 min max for Sentinel session
max_concurrent_spawns: 2

# Notification settings (terminal-notifier)
notify_on_send: true
notify_on_pending: true
notify_on_fail: true
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def check() -> bool:
    """Applied if table exists, all dirs exist, and config exists."""
    if not all(d.exists() for d in DIRS):
        return False
    if not CONFIG_PATH.exists():
        return False
    if not COMMS_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(COMMS_DB))
        try:
            return _table_exists(conn, "agent_triggers")
        finally:
            conn.close()
    except Exception:
        return False


def up() -> bool:
    """Create dirs, schema, and default config. Idempotent."""
    # 1. Directories
    for d in DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            print(f"       Created {d}")
        else:
            print(f"       Exists:  {d}")

    # 2. Config
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(DEFAULT_CONFIG)
        print(f"       Wrote   {CONFIG_PATH}")
    else:
        print(f"       Exists:  {CONFIG_PATH}")

    # 3. Schema — create comms.db if it doesn't exist yet. agent_triggers is
    #    Sentinel-owned and self-contained; creating the file early is safe
    #    because comms-bus applies its own tables with IF NOT EXISTS.
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(COMMS_DB))
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()
        print(f"       Schema applied to {COMMS_DB}")
    except Exception as e:
        print(f"       ERROR applying schema: {e}")
        return False

    return True


if __name__ == "__main__":
    if check():
        print("Migration 070 already applied")
    else:
        print("Done" if up() else "Failed")
