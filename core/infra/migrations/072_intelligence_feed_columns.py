"""
Migration 029: Add feed-related columns to intelligence tables.

The canonical schema (qareen.sql) defines columns that were added after
the initial intelligence_sources and intelligence_briefs tables were created.
This migration brings the live DB in line with the canonical schema so the
feed ingest engine can function.

intelligence_sources additions:
    - platform   TEXT    (twitter, youtube, github, hn, blog, arxiv)
    - route      TEXT    (RSSHub route path, e.g. /twitter/user/karpathy)
    - route_url  TEXT    (direct RSS URL for sources with native RSS)
    - priority   TEXT    (high, normal, low) DEFAULT 'normal'
    - keywords   TEXT    (JSON array of triage keywords)
    - items_total INTEGER (total items ever fetched) DEFAULT 0

intelligence_briefs additions:
    - platform       TEXT    (twitter, youtube, github, hn, blog, arxiv)
    - content        TEXT    (full extracted markdown)
    - url            TEXT    (source URL, dedup key) UNIQUE
    - author         TEXT
    - relevance_tags TEXT    (JSON array of matched keywords)
    - published_at   TEXT    (original publication time)
    - status         TEXT    (unread, read, saved, dismissed)
    - vault_path     TEXT    (set when saved to vault)

Also ensures indexes exist:
    - idx_sources_platform ON intelligence_sources(platform)
    - idx_briefs_url ON intelligence_briefs(url)
    - idx_briefs_platform ON intelligence_briefs(platform)
    - idx_briefs_status ON intelligence_briefs(status)
    - idx_briefs_published ON intelligence_briefs(published_at DESC)
"""

DESCRIPTION = "Add feed columns to intelligence_sources and intelligence_briefs"

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

# Columns to add: (table, column_name, type_and_default)
COLUMNS = [
    # intelligence_sources
    ("intelligence_sources", "platform",    "TEXT"),
    ("intelligence_sources", "route",       "TEXT"),
    ("intelligence_sources", "route_url",   "TEXT"),
    ("intelligence_sources", "priority",    "TEXT DEFAULT 'normal'"),
    ("intelligence_sources", "keywords",    "TEXT"),
    ("intelligence_sources", "items_total", "INTEGER DEFAULT 0"),
    # intelligence_briefs
    ("intelligence_briefs", "platform",       "TEXT"),
    ("intelligence_briefs", "content",        "TEXT"),
    ("intelligence_briefs", "url",            "TEXT"),
    ("intelligence_briefs", "author",         "TEXT"),
    ("intelligence_briefs", "relevance_tags", "TEXT"),
    ("intelligence_briefs", "published_at",   "TEXT"),
    ("intelligence_briefs", "status",         "TEXT DEFAULT 'unread'"),
    ("intelligence_briefs", "vault_path",     "TEXT"),
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sources_platform ON intelligence_sources(platform)",
    "CREATE INDEX IF NOT EXISTS idx_briefs_url ON intelligence_briefs(url)",
    "CREATE INDEX IF NOT EXISTS idx_briefs_platform ON intelligence_briefs(platform)",
    "CREATE INDEX IF NOT EXISTS idx_briefs_status ON intelligence_briefs(status)",
    "CREATE INDEX IF NOT EXISTS idx_briefs_published ON intelligence_briefs(published_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_briefs_url_unique ON intelligence_briefs(url)",
]


def _get_columns(conn, table: str) -> set[str]:
    """Get existing column names for a table."""
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in info}
    except sqlite3.OperationalError:
        return set()


def check() -> bool:
    """Applied if all columns already exist in both tables."""
    if not DB_PATH.exists():
        return True  # No DB = nothing to migrate, skip gracefully

    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table, col_name, _ in COLUMNS:
            existing = _get_columns(conn, table)
            if not existing:
                return True  # Table doesn't exist, schema creation will handle it
            if col_name not in existing:
                return False
        return True
    finally:
        conn.close()


def up() -> bool:
    """Add missing columns and indexes to intelligence tables."""
    if not DB_PATH.exists():
        return True  # No DB yet; schema creation covers a fresh install.

    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table, col_name, col_def in COLUMNS:
            existing = _get_columns(conn, table)
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

        # Create indexes (all idempotent with IF NOT EXISTS)
        for idx_sql in INDEXES:
            try:
                conn.execute(idx_sql)
            except sqlite3.OperationalError:
                # Unique index on nullable column may conflict with existing data
                pass

        conn.commit()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()

    return True
