"""
Migration 046: Add a first-class `short_id` column to the projects table.

Background
----------
A project's short-id (e.g. "dod" for "Deen Over Dunya") is the user-facing
handle used as the scoped task-id prefix (dod#1) and as an alias for
`--project` resolution. It had no column: `work projects create --short-id dod`
stored the value encoded in the project's `description` as the literal string
"short_id:dod", and nothing resolved it back.

Consequently `work add "Task" --project dod` set the task's project_id foreign
key to the literal "dod" — but the project's canonical id is "p1" — so the FK
(tasks.project_id -> projects(id)) had no matching parent row and raised:

    sqlite3.IntegrityError: FOREIGN KEY constraint failed

This migration brings the live DB in line with the canonical schema
(qareen.sql) by:
  1. Adding `projects.short_id TEXT`.
  2. Creating a unique partial index on it.
  3. Backfilling `short_id` from any "short_id:X" token previously encoded in
     the description, and removing that token from the description so the two
     representations don't drift.

Idempotent: re-running is a no-op once the column exists and descriptions are
clean.
"""

DESCRIPTION = "Add short_id column to projects and backfill from description"

import re
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

# Matches "short_id:dod" optionally surrounded by ", " separators, as written
# by the old add_project() description encoding.
_SHORT_ID_TOKEN = re.compile(r"(?:^|,\s*)short_id:([A-Za-z0-9_-]+)")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _clean_description(desc: str | None) -> str | None:
    """Strip the encoded short_id token from a description.

    Returns the cleaned description, or None if it becomes empty.
    """
    if not desc:
        return desc
    cleaned = _SHORT_ID_TOKEN.sub("", desc)
    # Tidy up leftover separators / whitespace.
    cleaned = re.sub(r"^\s*,\s*", "", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = cleaned.strip().strip(",").strip()
    return cleaned or None


def check() -> bool:
    """Applied when the column exists and no description still encodes short_id."""
    if not DB_PATH.exists():
        return True  # No DB yet — schema creation will include the column.
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cols = _columns(conn, "projects")
        if not cols:
            return True  # projects table doesn't exist; schema creation handles it.
        if "short_id" not in cols:
            return False
        # Also ensure no legacy encoded tokens remain to backfill.
        rows = conn.execute(
            "SELECT description FROM projects "
            "WHERE description LIKE '%short_id:%'"
        ).fetchall()
        return len(rows) == 0
    finally:
        conn.close()


def apply() -> str:
    if not DB_PATH.exists():
        return "Skipped: qareen.db does not exist yet"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cols = _columns(conn, "projects")
        if not cols:
            return "Skipped: projects table does not exist yet"

        added_column = False
        if "short_id" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN short_id TEXT")
            added_column = True

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_short_id "
            "ON projects(short_id) WHERE short_id IS NOT NULL"
        )

        # Backfill short_id from the encoded description token.
        backfilled = 0
        rows = conn.execute(
            "SELECT id, description, short_id FROM projects "
            "WHERE description LIKE '%short_id:%'"
        ).fetchall()
        for proj_id, desc, existing in rows:
            m = _SHORT_ID_TOKEN.search(desc or "")
            if not m:
                continue
            value = existing or m.group(1)
            new_desc = _clean_description(desc)
            try:
                conn.execute(
                    "UPDATE projects SET short_id = ?, description = ? "
                    "WHERE id = ?",
                    (value, new_desc, proj_id),
                )
                backfilled += 1
            except sqlite3.IntegrityError:
                # A short_id collision (unique index) — leave description token
                # so it remains visible; skip rather than fail the migration.
                conn.execute(
                    "UPDATE projects SET description = ? WHERE id = ?",
                    (desc, proj_id),
                )

        conn.commit()
    except sqlite3.OperationalError as e:
        return f"Error: {e}"
    finally:
        conn.close()

    parts = []
    if added_column:
        parts.append("added projects.short_id")
    if backfilled:
        parts.append(f"backfilled {backfilled} project(s)")
    return "; ".join(parts) if parts else "Already applied"
