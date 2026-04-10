"""
Migration 035: Add vault_inventory table.

The inventory is a cached snapshot of the vault's state: one row per
markdown file, recording its frontmatter health, contract violations,
orphan status, and compilation state. It is:

- **Non-destructive** — the scanner only reads vault files, never writes.
- **Rebuildable** — dropping and re-running the scanner produces the same
  rows modulo timestamps. No operator state lives here.
- **Queryable** — the reconcile check, Part 7's Library view, and Part 8's
  bootstrap flow all read from this table instead of re-walking the vault.

This table is populated by:
    core.engine.intelligence.inventory.scanner.scan_vault()

And consumed by:
    core.infra.reconcile.checks.vault_contract  (reports drift)
    core.qareen.api.knowledge  (Library view, pending)
    core.qareen.api.bootstrap  (dry-run + bootstrap exec, pending)

Columns:
    path               TEXT PRIMARY KEY  — vault-relative path (e.g. knowledge/captures/foo.md)
    stage              INTEGER           — 1..6 (inferred from folder if frontmatter missing)
    stage_declared     INTEGER           — what frontmatter says, or NULL
    type               TEXT              — capture, research, reference, synthesis, decision, expertise, initiative, index
    title              TEXT
    topic              TEXT              — frontmatter topic slug, if any
    has_frontmatter    INTEGER           — 0/1
    has_summary        INTEGER           — 0/1
    has_concepts       INTEGER           — 0/1
    has_topic          INTEGER           — 0/1
    has_source_url     INTEGER           — 0/1 (mandatory for captures)
    backlink_count     INTEGER           — how many other docs reference this one
    is_orphan          INTEGER           — 0/1 — no incoming links AND stage >= 3
    compilation_status TEXT              — pending, compiled, skipped
    issues             TEXT              — JSON array of contract-violation strings
    last_modified      TEXT              — file mtime
    last_scanned       TEXT              — when the scanner last touched this row
    file_size          INTEGER           — bytes
    word_count         INTEGER           — body word count
"""

DESCRIPTION = "Add vault_inventory table for cached vault state"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vault_inventory (
    path               TEXT PRIMARY KEY,
    stage              INTEGER,
    stage_declared     INTEGER,
    type               TEXT,
    title              TEXT,
    topic              TEXT,
    has_frontmatter    INTEGER NOT NULL DEFAULT 0,
    has_summary        INTEGER NOT NULL DEFAULT 0,
    has_concepts       INTEGER NOT NULL DEFAULT 0,
    has_topic          INTEGER NOT NULL DEFAULT 0,
    has_source_url     INTEGER NOT NULL DEFAULT 0,
    backlink_count     INTEGER NOT NULL DEFAULT 0,
    is_orphan          INTEGER NOT NULL DEFAULT 0,
    compilation_status TEXT NOT NULL DEFAULT 'pending',
    issues             TEXT,
    last_modified      TEXT,
    last_scanned       TEXT NOT NULL,
    file_size          INTEGER NOT NULL DEFAULT 0,
    word_count         INTEGER NOT NULL DEFAULT 0
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_inventory_stage ON vault_inventory(stage);",
    "CREATE INDEX IF NOT EXISTS idx_inventory_type ON vault_inventory(type);",
    "CREATE INDEX IF NOT EXISTS idx_inventory_topic ON vault_inventory(topic);",
    "CREATE INDEX IF NOT EXISTS idx_inventory_orphan ON vault_inventory(is_orphan) WHERE is_orphan = 1;",
    "CREATE INDEX IF NOT EXISTS idx_inventory_compilation ON vault_inventory(compilation_status);",
]


def check() -> bool:
    if not DB_PATH.exists():
        return True
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_inventory'"
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
