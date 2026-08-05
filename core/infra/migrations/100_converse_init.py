"""
Migration 100: Converse engine — Wave 0 foundation (schema, config, dirs).

NUMBERING NOTE: PLAN.md (~/.aos/tmp/sessions-build/PLAN.md §2/§8) specifies
this as migration `099_converse_init.py`. By the time this Wave-0 build ran,
099 had already been allocated to `099_cmux_socket_control.py` (landed
2026-08-05, after the plan was written) — migration numbers are a single
monotonic sequence, so 099 was no longer free. This migration takes 100
instead. PLAN.md's Phase B/C/D/E migrations (`100_converse_service.py`,
`101_envoy_to_converse.py`, `102_sentinel_on_converse.py`,
`103_converse_cleanup.py`) each shift up by one accordingly (101/102/103/104)
when those waves are built — flagged here so the next builder doesn't
collide again.

Converse is the Conversation-Session engine (PLAN.md §1) — Sentinel and
Envoy become its two *modes*, not separate systems. This migration is
Wave 0 / T1: purely additive, zero behavior change (PLAN.md §8 Phase A).
It does NOT install a daemon, LaunchAgent, or touch any existing table.

Creates:
1. `conversation_sessions`, `session_messages`, `session_actions` tables in
   comms.db — schema owned by core/engine/comms/converse/schema.sql, applied
   here via converse/db.py's connect() (idempotent: CREATE TABLE/INDEX IF
   NOT EXISTS). Self-contained (no FK into comms-bus-owned tables), so —
   matching migration 070's precedent for agent_triggers — this migration
   creates comms.db itself if it doesn't exist yet; comms-bus applies its
   own tables with IF NOT EXISTS on first run regardless.
2. ~/.aos/work/converse/   (per-session scratch workspaces, PLAN.md §4)
3. ~/.aos/logs/converse/   (service + per-turn logs, PLAN.md §4)
4. ~/.aos/config/converse.yaml from the framework default
   (config/defaults/converse.yaml), `enabled: true` — there is no daemon
   yet to act on it (T3, a later wave), so this ships inert.

Idempotent: safe to re-run. check() verifies the precise end-state (all 3
tables present with their expected columns, not just "comms.db exists").
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DESCRIPTION = "Converse engine: schema (3 tables) + config + dirs (Wave 0 foundation)"

HOME = Path.home()
AOS_ROOT = HOME / "aos"
DATA_DIR = HOME / ".aos" / "data"
COMMS_DB = DATA_DIR / "comms.db"
WORK_DIR = HOME / ".aos" / "work" / "converse"
LOG_DIR = HOME / ".aos" / "logs" / "converse"
CONFIG_PATH = HOME / ".aos" / "config" / "converse.yaml"
DEFAULT_CONFIG = AOS_ROOT / "config" / "defaults" / "converse.yaml"

DIRS = [WORK_DIR, LOG_DIR]

REQUIRED_TABLES = {"conversation_sessions", "session_messages", "session_actions"}

_FALLBACK_CONFIG = """# Converse configuration (fallback — see config/defaults/converse.yaml)
enabled: true
max_concurrent_handlers: 2
batch_quiet_seconds: 30
defaults:
  trust_level: 2
  max_messages: 30
  expires_days: 5
  tools: none
hard_floor:
  blocked_intent_words: [book, schedule, pay, buy, "send money", reserve, transfer]
  block_inner_circle_importance: 1
  max_sends_per_hour: 6
channels:
  imessage: {enabled: true}
  slack: {enabled: true, workspace_url: "https://<workspace>.slack.com", poll_interval_s: 25}
notify: {on_send: true, on_escalate: true, on_complete: true, on_fail: true}
"""


def _load_converse_db():
    """Import core/engine/comms/converse/db.py the same way migration 060
    imports people/db.py: locate it relative to this migration file's own
    position under core/infra/migrations (works whether this migration is
    running from ~/aos or ~/project/aos), sys.path-insert its directory,
    import it standalone. Returns the module, or None if the package isn't
    present (e.g. a partially-synced dev workspace)."""
    core_dir = next((p for p in Path(__file__).resolve().parents if p.name == "core"), None)
    if core_dir is None:
        return None
    converse_dir = core_dir / "engine" / "comms" / "converse"
    if not converse_dir.exists():
        return None
    if str(converse_dir) not in sys.path:
        sys.path.insert(0, str(converse_dir))
    try:
        import db as converse_db  # type: ignore
    except Exception as e:
        print(f"  Could not import converse/db.py: {e}")
        return None
    return converse_db


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def check() -> bool:
    """Applied if dirs + config exist, and (if comms.db exists) all 3
    converse tables are present. A schema-exists check, not a
    file-exists-on-comms.db check — a comms.db that predates converse but
    lacks the tables must not read as already-migrated."""
    if not all(d.exists() for d in DIRS):
        return False
    if not CONFIG_PATH.exists():
        return False
    if not COMMS_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(COMMS_DB))
        try:
            return REQUIRED_TABLES.issubset(_existing_tables(conn))
        finally:
            conn.close()
    except Exception:
        return False


def up() -> bool:
    """Create dirs, config, and schema. Idempotent."""
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
        if DEFAULT_CONFIG.exists():
            CONFIG_PATH.write_text(DEFAULT_CONFIG.read_text())
            print(f"       Wrote   {CONFIG_PATH} (from framework default)")
        else:
            CONFIG_PATH.write_text(_FALLBACK_CONFIG)
            print(f"       Wrote   {CONFIG_PATH} (fallback — framework default not found)")
    else:
        print(f"       Exists:  {CONFIG_PATH}")

    # 3. Schema — via converse/db.py's connect(), which lazily applies
    #    schema.sql (CREATE TABLE/INDEX IF NOT EXISTS). Creates comms.db
    #    itself if absent, matching migration 070's precedent for
    #    agent_triggers: these tables are self-contained, and comms-bus
    #    applies its own tables with IF NOT EXISTS regardless of file age.
    converse_db = _load_converse_db()
    if converse_db is None:
        print("       ERROR: could not locate/import converse/db.py — aborting")
        return False

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = converse_db.connect(COMMS_DB)
        try:
            missing = REQUIRED_TABLES - _existing_tables(conn)
            if missing:
                raise RuntimeError(f"tables missing after connect(): {sorted(missing)}")
        finally:
            conn.close()
        print(f"       Schema applied to {COMMS_DB} ({', '.join(sorted(REQUIRED_TABLES))})")
    except Exception as e:
        print(f"       ERROR applying schema: {e}")
        return False

    return True


if __name__ == "__main__":
    if check():
        print("Migration 100 already applied")
    else:
        print("Done" if up() else "Failed")
