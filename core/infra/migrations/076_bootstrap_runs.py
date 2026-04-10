"""
Migration 036: Add bootstrap_runs table.

Tracks vault bootstrap executions: operator-initiated one-shots that
sweep existing vault docs through the compilation engine to backfill
missing frontmatter, assign topics, and create links.

Each row represents ONE bootstrap attempt with:
    - a git commit ref (pre-bootstrap snapshot) for rollback
    - total + processed counters for progress tracking
    - status machine: pending → running → paused → done | failed | cancelled
    - current document path while running (for the UI's live "now processing")
    - aggregate stats on completion (auto-accepted, pending review, errors)

The Bootstrap flow is strictly non-destructive:
    - git snapshot is taken BEFORE any file mutation
    - operator-set frontmatter fields are preserved (merge, not overwrite)
    - doc body is never touched — only frontmatter
    - confidence below SHADOW_ACCEPT_THRESHOLD stays pending in proposals
"""

DESCRIPTION = "Add bootstrap_runs table for vault bootstrap execution history"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bootstrap_runs (
    id               TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending, running, paused, done, failed, cancelled
    git_ref          TEXT,                             -- pre-bootstrap commit hash
    git_branch       TEXT,
    total_docs       INTEGER NOT NULL DEFAULT 0,
    processed_docs   INTEGER NOT NULL DEFAULT 0,
    skipped_docs     INTEGER NOT NULL DEFAULT 0,
    auto_accepted    INTEGER NOT NULL DEFAULT 0,
    pending_review   INTEGER NOT NULL DEFAULT 0,
    errors           INTEGER NOT NULL DEFAULT 0,
    current_path     TEXT,                             -- vault-relative path of doc being processed
    current_started  TEXT,
    error_log        TEXT,                             -- JSON array of {path, error}
    model            TEXT,                             -- which LLM model was used
    provider         TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd    REAL
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_bootstrap_status ON bootstrap_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_bootstrap_started ON bootstrap_runs(started_at DESC);",
]


def check() -> bool:
    if not DB_PATH.exists():
        return True
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bootstrap_runs'"
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
