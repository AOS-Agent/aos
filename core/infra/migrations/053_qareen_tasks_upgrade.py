"""
Migration 053: Qareen Tasks data model upgrade.

Adds:
- status_categories + statuses tables (Linear-style status model)
- entity_history table (field-level change tracking)
- comments table (threaded comments on entities)
- saved_views table (composable filter/sort/group configurations)
- task_participants table (watchers, reviewers, collaborators)
- attachments table (files, links, vault refs)
- New columns on tasks: scheduled_at, snoozed_until, estimate_minutes,
  story_points, actual_minutes, energy, context, area_id, assignee_type,
  recurrence_type, template_id, recurrence_index
- Composite indexes for common query patterns
- Default status definitions

Part of the Qareen Tasks 100x build.

Numbering note: this shipped as migration 023 on the unshipped
council-substrate branch (see core/infra/migrations/050_work_db_ownership.py
for the full renumbering rationale) and was applied to this dev instance
out-of-band. Renumbered to 053 to land after main's 001-052.

Ordering note: migration 054 (qareen service deploy) seeds a fresh qareen.db
directly from schemas/qareen.sql, which already includes every table this
migration adds. On a fresh machine 053 runs first, finds no qareen.db yet,
and no-ops — 054's schema is the source of truth there. This migration exists
to retrofit qareen.db instances that predate the Tasks 100x upgrade (this dev
machine's qareen.db, and any other instance that already ran 001-022).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DESCRIPTION = "Qareen Tasks 100x data model upgrade"
MIGRATION_ID = "053_qareen_tasks_upgrade"


def _db_path() -> Path:
    """Find qareen.db — check both possible locations."""
    p1 = Path.home() / ".aos" / "data" / "qareen.db"
    p2 = Path.home() / ".aos" / "services" / "qareen" / "qareen.db"
    return p1 if p1.exists() else p2


def check() -> bool:
    """Already applied if qareen.db doesn't exist yet (fresh machine — migration
    054 seeds it directly from schemas/qareen.sql, which already carries this
    upgrade) or if the comments table is already present (applied previously,
    including out-of-band on this dev machine).
    """
    db_path = _db_path()
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(str(db_path))
        # If the tasks table itself is absent, there is nothing to retrofit —
        # the db file exists but carries no pre-upgrade schema (e.g. an empty
        # file side-created by a service start or a read-through connect).
        # 054 seeds the full schema in that case. Found by clean-box VM:
        # empty qareen.db made up() fail "no such table: tasks" and halt
        # every upgrade at 53.
        has_tasks = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if has_tasks is None:
            conn.close()
            return True
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='comments'"
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception:
        # Can't inspect the db — don't block the migration chain on a read error.
        return True


def up() -> bool:
    """Execute the migration."""
    db_path = _db_path()
    if not db_path.exists():
        logger.warning("qareen.db not found, skipping migration")
        return True

    conn = sqlite3.connect(str(db_path))

    # Mirror check()'s guard: a db file without the tasks table has no
    # pre-upgrade schema to retrofit — 054 seeds everything. Never assume
    # the table exists just because the file does.
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone() is None:
        conn.close()
        print("       qareen.db has no tasks table — nothing to retrofit (054 seeds fresh)")
        return True

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── New tables ──────────────────────────────────────────

        conn.executescript("""
            -- Status definitions (custom statuses within fixed categories)
            CREATE TABLE IF NOT EXISTS statuses (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                category    TEXT NOT NULL CHECK(category IN ('triage','backlog','unstarted','started','completed','cancelled')),
                color       TEXT,
                project_id  TEXT REFERENCES projects(id),
                position    INTEGER NOT NULL DEFAULT 0,
                is_default  BOOLEAN DEFAULT 0
            );

            -- Entity history (field-level change tracking)
            CREATE TABLE IF NOT EXISTS entity_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                field_name  TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT,
                actor       TEXT NOT NULL,
                actor_type  TEXT NOT NULL CHECK(actor_type IN ('operator','agent','system','automation')),
                timestamp   TEXT NOT NULL,
                session_id  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_history_entity ON entity_history(entity_type, entity_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_history_field ON entity_history(entity_type, entity_id, field_name);

            -- Comments (threaded, on any entity)
            CREATE TABLE IF NOT EXISTS comments (
                id          TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                parent_id   TEXT REFERENCES comments(id),
                author_id   TEXT NOT NULL,
                author_type TEXT NOT NULL CHECK(author_type IN ('operator','agent','system')),
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                modified_at TEXT,
                is_edited   BOOLEAN DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_comments_entity ON comments(entity_type, entity_id, created_at);

            -- Saved views (composable filters, sorts, groups)
            CREATE TABLE IF NOT EXISTS saved_views (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                icon        TEXT,
                layout      TEXT NOT NULL DEFAULT 'stream' CHECK(layout IN ('stream','board','today','list','calendar','timeline')),
                entity_type TEXT NOT NULL DEFAULT 'task',
                filters     TEXT NOT NULL DEFAULT '{}',
                sort_rules  TEXT DEFAULT '[]',
                group_by    TEXT,
                sub_group_by TEXT,
                columns     TEXT DEFAULT '[]',
                scope       TEXT NOT NULL DEFAULT 'personal' CHECK(scope IN ('personal','shared')),
                owner_id    TEXT NOT NULL,
                position    INTEGER DEFAULT 0,
                is_pinned   BOOLEAN DEFAULT 0,
                created_at  TEXT NOT NULL,
                modified_at TEXT
            );

            -- Task participants (watchers, reviewers, collaborators)
            CREATE TABLE IF NOT EXISTS task_participants (
                task_id     TEXT NOT NULL REFERENCES tasks(id),
                entity_id   TEXT NOT NULL,
                entity_type TEXT NOT NULL CHECK(entity_type IN ('person','agent','operator')),
                role        TEXT NOT NULL CHECK(role IN ('assignee','reviewer','watcher','collaborator')),
                added_at    TEXT NOT NULL,
                PRIMARY KEY (task_id, entity_id, role)
            );

            -- Attachments (files, links, vault refs, code refs)
            CREATE TABLE IF NOT EXISTS attachments (
                id          TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                file_type   TEXT NOT NULL CHECK(file_type IN ('file','link','vault_note','code_file')),
                name        TEXT NOT NULL,
                url         TEXT,
                vault_path  TEXT,
                repo_path   TEXT,
                line_start  INTEGER,
                line_end    INTEGER,
                uploaded_by TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id);
        """)

        # ── New columns on tasks ──────────────────────────────────

        # Get existing columns to avoid duplicate ALTER TABLE errors
        cursor = conn.execute("PRAGMA table_info(tasks)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        new_columns = [
            ("scheduled_at", "TEXT"),
            ("snoozed_until", "TEXT"),
            ("estimate_minutes", "INTEGER"),
            ("story_points", "REAL"),
            ("actual_minutes", "INTEGER"),
            ("energy", "TEXT"),
            ("context", "TEXT"),
            ("area_id", "TEXT"),
            ("assignee_type", "TEXT DEFAULT 'operator'"),
            ("recurrence_type", "TEXT DEFAULT 'fixed'"),
            ("template_id", "TEXT"),
            ("recurrence_index", "INTEGER"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column tasks.{col_name}")

        # ── Composite indexes for common queries ──────────────────

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks(status, priority);
            CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_at) WHERE scheduled_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id) WHERE parent_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_tasks_template ON tasks(template_id) WHERE template_id IS NOT NULL;
        """)

        # ── Default status definitions ──────────────────────────

        default_statuses = [
            ("triage", "Triage", "triage", "#BF5AF2", 0, 0),
            ("backlog", "Backlog", "backlog", "#6B6560", 1, 0),
            ("todo", "Todo", "unstarted", "#6B6560", 2, 1),
            ("active", "In Progress", "started", "#0A84FF", 3, 0),
            ("waiting", "Waiting", "started", "#FFD60A", 4, 0),
            ("in_review", "In Review", "started", "#BF5AF2", 5, 0),
            ("done", "Done", "completed", "#30D158", 6, 1),
            ("cancelled", "Cancelled", "cancelled", "#6B6560", 7, 0),
        ]

        for sid, name, category, color, position, is_default in default_statuses:
            conn.execute(
                "INSERT OR IGNORE INTO statuses (id, name, category, color, position, is_default) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, name, category, color, position, is_default),
            )

        conn.commit()
        logger.info("Migration 053 complete: Qareen tasks data model upgraded")
        return True

    except Exception as e:
        logger.error(f"Migration 053 failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()
