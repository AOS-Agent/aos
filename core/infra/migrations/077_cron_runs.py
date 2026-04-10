"""
Migration 037: Add cron_runs table.

Per-invocation telemetry for every cron defined in config/crons.yaml.
Replaces the "infer cron health from data timestamps" heuristic with
real start/end/duration/exit/output data.

Row contract:
    id          — auto-increment
    cron_name   — logical name (e.g. 'feed-ingest', 'vault-maintenance')
    started_at  — ISO8601 when the wrapper kicked off the command
    ended_at    — ISO8601 when it exited (null if still running)
    duration_ms — end - start in ms (null if still running)
    exit_code   — 0 = success, non-zero = failure, null = still running
    status      — 'running', 'ok', 'failed', 'timeout', 'killed'
    stdout_tail — last ~8KB of stdout (for UI preview)
    stderr_tail — last ~8KB of stderr (for UI preview)
    stats_json  — optional structured stats the cron reports via sentinel line
    host        — hostname of the machine that ran it
    pid         — process ID of the wrapped command

Consumers:
    - /api/knowledge/pipeline — replaces _build_cron_rows heuristic
    - Future: /api/pipeline/crons/{name} for per-cron history + logs
"""

DESCRIPTION = "Add cron_runs table for real cron telemetry"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cron_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cron_name    TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    duration_ms  INTEGER,
    exit_code    INTEGER,
    status       TEXT NOT NULL DEFAULT 'running',
    stdout_tail  TEXT,
    stderr_tail  TEXT,
    stats_json   TEXT,
    host         TEXT,
    pid          INTEGER
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_cron_runs_name ON cron_runs(cron_name, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_cron_runs_status ON cron_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_cron_runs_started ON cron_runs(started_at DESC);",
]


def check() -> bool:
    if not DB_PATH.exists():
        return True
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cron_runs'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def up() -> bool:
    if not DB_PATH.exists():
        return True  # No DB yet; schema creation covers a fresh install.
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(CREATE_SQL)
        for idx in INDEX_SQL:
            conn.execute(idx)
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
