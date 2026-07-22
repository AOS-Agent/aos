"""
Migration 092: Work runner infrastructure (Kanban Phase 4 — the generic runner).

Creates the runner's ledger table, runtime directories, and instance config —
but deliberately does NOT deploy or load the LaunchAgent. This is autonomous
agent spawning; it ships OFF and the operator opts in explicitly with
`work runner enable` (renders + loads the plist, flips runner.enabled: true).
Because the service manifest is status=optional, ServiceLoadedCheck treats an
absent (un-opted-in) runner as fine.

Creates:
1. `task_runs` table in work.db (the delegation → spawn ledger; UNIQUE on
   (task_id, delegation_ts) gives one-delegation-one-spawn idempotency). The
   runner also creates this at runtime, so this is belt-and-suspenders for a
   work.db that already exists.
2. ~/.aos/logs/work-runner/  and  ~/.aos/work/runner/worktrees/
3. ~/.aos/config/work-runner.yaml from the framework default (enabled: false).

Idempotent: safe to re-run. work.db may not exist yet on a fresh machine (the
kernel seeds it later, migration 050); the runner's runtime `_ensure_schema`
covers that case, so this migration only touches work.db if it is already there.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DESCRIPTION = "Work runner infrastructure (ledger table, dirs, config — ships OFF)"

HOME = Path.home()
AOS_ROOT = HOME / "aos"
DATA_DIR = HOME / ".aos" / "data"
WORK_DB = DATA_DIR / "work.db"
LOG_DIR = HOME / ".aos" / "logs" / "work-runner"
WORKTREE_DIR = HOME / ".aos" / "work" / "runner" / "worktrees"
CONFIG_PATH = HOME / ".aos" / "config" / "work-runner.yaml"
DEFAULT_CONFIG = AOS_ROOT / "config" / "defaults" / "work-runner.yaml"

DIRS = [LOG_DIR, WORKTREE_DIR]

TASK_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS task_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    delegation_ts TEXT NOT NULL,
    holder        TEXT NOT NULL,
    agent         TEXT NOT NULL,
    state         TEXT NOT NULL,
    pid           INTEGER,
    attempt       INTEGER NOT NULL DEFAULT 1,
    log_path      TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    ended_at      TEXT,
    reason        TEXT,
    UNIQUE(task_id, delegation_ts)
);
CREATE INDEX IF NOT EXISTS idx_task_runs_state ON task_runs(state);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
"""

_FALLBACK_CONFIG = """# Work Runner configuration (fallback — see config/defaults/work-runner.yaml)
enabled: false
max_concurrent: 2
spawn_timeout_seconds: 600
poll_interval_seconds: 15
trust_floor: 1
default_capability: task_execution
allowed_tools: "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch"
model: null
review_status: in_review
needs_attention_status: waiting
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def check() -> bool:
    """Applied when dirs + config exist, and (if work.db exists) task_runs does."""
    if not all(d.exists() for d in DIRS):
        return False
    if not CONFIG_PATH.exists():
        return False
    if WORK_DB.exists():
        try:
            conn = sqlite3.connect(str(WORK_DB))
            try:
                if not _table_exists(conn, "task_runs"):
                    return False
            finally:
                conn.close()
        except Exception:
            return False
    return True


def up() -> bool:
    """Create dirs, config, and (if work.db exists) the ledger table. Idempotent."""
    # 1. Directories
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
        print(f"       Dir:    {d}")

    # 2. Config (from the framework default; a small fallback if it's missing)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULT_CONFIG.exists():
            CONFIG_PATH.write_text(DEFAULT_CONFIG.read_text())
            print(f"       Wrote   {CONFIG_PATH} (from default, enabled: false)")
        else:
            CONFIG_PATH.write_text(_FALLBACK_CONFIG)
            print(f"       Wrote   {CONFIG_PATH} (fallback, enabled: false)")
    else:
        print(f"       Exists: {CONFIG_PATH}")

    # 3. Ledger table — only if work.db already exists. The runner creates this
    #    at runtime otherwise, so a not-yet-seeded machine is fine.
    if WORK_DB.exists():
        try:
            conn = sqlite3.connect(str(WORK_DB))
            try:
                conn.executescript(TASK_RUNS_SQL)
                conn.commit()
            finally:
                conn.close()
            print(f"       Schema: task_runs applied to {WORK_DB}")
        except Exception as e:
            print(f"       ERROR applying task_runs schema: {e}")
            return False
    else:
        print("       work.db not present yet — runner creates task_runs at runtime")

    return True


if __name__ == "__main__":
    if check():
        print("Migration 092 already applied")
    else:
        print("Done" if up() else "Failed")
