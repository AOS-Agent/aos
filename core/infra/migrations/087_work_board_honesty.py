"""
Migration 087: Board-honesty aux tables + inbox provenance (Kanban Phase 0).

Renumbered from 086 → 087: the original 086_work_board_honesty.py shipped in the
Phase 0 release (v0.6.21, commit 025d91c) but was clobbered when the envoy commit
(87e731d, branched pre-Phase-0) merged into main and reverted every shared file
Phase 0 touched. 086 was subsequently reclaimed by 086_rebuild_release_frontends.
This restores the Phase 0 bridge at the next free watermark (aos#143 monotonicity).

Migration 050 seeded work.db from the tasks tables only, so a live work.db
predates three tables that the canonical schema (core/qareen/schemas/qareen.sql)
already defines and the API already queries: statuses, entity_history, comments.
Without them the board's /api/tasks/{id}/activity and /api/statuses endpoints
return empty regardless of activity, and there is no audit trail of who moved a
task or when.

This ensures those tables exist (and seeds the default Linear-style status set),
and adds inbox.source + inbox.snoozed_until so the ambient proposer's provenance
survives and items can be deferred without deletion.

The WorkAdapter also ensures this schema at construction time (so fresh installs
and tests work without a migration having run first); this migration is the
explicit, auditable instance-layer bridge required by the atomic-migration rule.
Idempotent — every statement is IF NOT EXISTS / INSERT OR IGNORE / column-guarded.
"""

DESCRIPTION = "Kanban Phase 0: entity_history/statuses/comments + inbox provenance"

import os
import sqlite3
from pathlib import Path

_STATUS_SEED = [
    ("triage", "Triage", "triage", "#BF5AF2", 0, 0),
    ("backlog", "Backlog", "backlog", "#6B6560", 1, 0),
    ("todo", "Todo", "unstarted", "#6B6560", 2, 1),
    ("active", "In Progress", "started", "#0A84FF", 3, 0),
    ("waiting", "Waiting", "started", "#FFD60A", 4, 0),
    ("done", "Done", "completed", "#30D158", 5, 1),
    ("cancelled", "Cancelled", "cancelled", "#6B6560", 6, 0),
]


def _work_db_path() -> Path:
    """Resolve the same work DB the kernel/adapter resolve to."""
    env = os.environ.get("AOS_WORK_DB")
    if env:
        return Path(env).expanduser()
    work_db = Path.home() / ".aos" / "data" / "work.db"
    try:
        if work_db.exists() and work_db.stat().st_size > 0:
            c = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True)
            try:
                if c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone():
                    return work_db
            finally:
                c.close()
    except sqlite3.Error:
        pass
    return Path.home() / ".aos" / "data" / "qareen.db"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _inbox_cols(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}


def check() -> bool:
    db = _work_db_path()
    if not db.exists():
        return True  # nothing to migrate yet — adapter will create on first use
    conn = sqlite3.connect(str(db))
    try:
        tables_ok = all(
            _has_table(conn, t) for t in ("statuses", "entity_history", "comments")
        )
        cols = _inbox_cols(conn) if _has_table(conn, "inbox") else {"source", "snoozed_until"}
        cols_ok = {"source", "snoozed_until"} <= cols
        return tables_ok and cols_ok
    finally:
        conn.close()


def up() -> bool:
    db = _work_db_path()
    if not db.exists():
        print("       work DB not present yet — adapter will create aux schema on first use")
        return True
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entity_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                field_name  TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT,
                actor       TEXT NOT NULL,
                actor_type  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                session_id  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_history_entity
                ON entity_history(entity_type, entity_id, timestamp);

            CREATE TABLE IF NOT EXISTS statuses (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                category    TEXT NOT NULL,
                color       TEXT,
                project_id  TEXT,
                position    INTEGER NOT NULL DEFAULT 0,
                is_default  BOOLEAN DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS comments (
                id          TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                parent_id   TEXT,
                author_id   TEXT NOT NULL,
                author_type TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                modified_at TEXT,
                is_edited   BOOLEAN DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_comments_entity
                ON comments(entity_type, entity_id, created_at);
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO statuses "
            "(id, name, category, color, position, is_default) VALUES (?, ?, ?, ?, ?, ?)",
            _STATUS_SEED,
        )
        if _has_table(conn, "inbox"):
            cols = _inbox_cols(conn)
            if "source" not in cols:
                conn.execute("ALTER TABLE inbox ADD COLUMN source TEXT")
            if "snoozed_until" not in cols:
                conn.execute("ALTER TABLE inbox ADD COLUMN snoozed_until TEXT")
        conn.commit()
        print(f"       Ensured board-honesty schema on {db.name}")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
