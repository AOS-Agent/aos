"""
Migration 034: Add compilation_proposals table for shadow-mode compilation.

Shadow-mode sits between the compile engine and the vault. Every compilation
pass writes a row here first; only proposals with high confidence auto-accept
through to the vault. Lower-confidence proposals wait for operator approval.

This is the safety net that protects the vault from LLM weirdness. At
auto-accept threshold (0.85 topic confidence), most captures go through
untouched; the ones that need attention are surfaced in the Knowledge UI.

Table layout:
    id                TEXT PRIMARY KEY  — short uuid
    created_at        TEXT NOT NULL
    source            TEXT              — 'intelligence_brief', 'bootstrap', 'extract_skill'
    source_id         TEXT              — brief id, vault path, etc.
    status            TEXT NOT NULL     — pending, auto_accepted, approved, rejected
    auto_accepted     INTEGER DEFAULT 0 — 0/1 — true if crossed threshold
    topic_confidence  REAL              — LLM self-reported confidence (0-1)
    extraction_json   TEXT              — serialized ExtractionResult.to_dict()
    compilation_json  TEXT              — serialized CompilationResult.to_dict()
    vault_path        TEXT              — set when written to vault
    reviewed_at       TEXT              — timestamp of operator decision
    reviewed_by       TEXT              — 'auto', 'operator', or the operator name
    reject_reason     TEXT              — optional note on rejection
"""

DESCRIPTION = "Add compilation_proposals table for shadow-mode compilation"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS compilation_proposals (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'intelligence_brief',
    source_id        TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    auto_accepted    INTEGER NOT NULL DEFAULT 0,
    topic_confidence REAL,
    extraction_json  TEXT,
    compilation_json TEXT,
    vault_path       TEXT,
    reviewed_at      TEXT,
    reviewed_by      TEXT,
    reject_reason    TEXT
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_proposals_status ON compilation_proposals(status);",
    "CREATE INDEX IF NOT EXISTS idx_proposals_source ON compilation_proposals(source, source_id);",
    "CREATE INDEX IF NOT EXISTS idx_proposals_created ON compilation_proposals(created_at DESC);",
]


def check() -> bool:
    """Applied if compilation_proposals table exists."""
    if not DB_PATH.exists():
        return True
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='compilation_proposals'"
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
