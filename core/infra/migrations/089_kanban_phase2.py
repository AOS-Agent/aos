"""Migration 089: Kanban Phase 2 — the activity log (the narrative layer).

Adds ``task_activity`` — one append-only, immutable row per *logical* task
event, carrying a human-readable ``body`` and a structured ``data`` payload.
This is the NARRATIVE layer; ``entity_history`` (migration 087) stays as the
FORENSIC per-field layer. They are deliberately NOT merged — a narrative
answers "what happened", a field log answers "what value changed" (see
core/qareen/ontology/activity.py for the full rationale).

Append-only is enforced two ways: BEFORE UPDATE / BEFORE DELETE triggers that
RAISE(ABORT), and the adapter exposing no mutate-past-entry path in code.

Backfill: every existing ``entity_history`` row is echoed into the narrative
log so no task's story starts blank on an already-live machine —
  status  → kind=status_changed
  delegate→ kind=delegated
  other   → kind=edited
(held_by / pipeline_stage are companion field-diffs of a delegate/status event
and are skipped to avoid double-narrating the same moment). Idempotent: each
backfilled row carries source_event_id='history:<id>' and is inserted only if
that marker is absent, so re-running the migration adds nothing.

The WorkAdapter also ensures this schema at construction (fresh installs and
tests), so this migration is the explicit instance-layer bridge required by the
atomic-migration rule. Fully idempotent.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DESCRIPTION = "Kanban Phase 2: task_activity (append-only narrative) + backfill"


def _work_db_path() -> Path:
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    ts              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    body            TEXT NOT NULL,
    data            TEXT,
    source_event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_task ON task_activity(task_id, id);
CREATE TRIGGER IF NOT EXISTS task_activity_no_update
    BEFORE UPDATE ON task_activity
    BEGIN SELECT RAISE(ABORT, 'task_activity is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_activity_no_delete
    BEFORE DELETE ON task_activity
    BEGIN SELECT RAISE(ABORT, 'task_activity is append-only'); END;
"""


def check() -> bool:
    db = _work_db_path()
    if not db.exists():
        return True  # adapter ensures on first use
    conn = sqlite3.connect(str(db))
    try:
        if not _has_table(conn, "task_activity"):
            return False
        if not _has_table(conn, "entity_history"):
            return True  # nothing to backfill
        # Applied iff every history row already has its narrative echo.
        pending = conn.execute(
            "SELECT count(*) FROM entity_history h "
            "WHERE h.entity_type = 'task' "
            "  AND h.field_name IN ('status','delegate') "
            "  AND NOT EXISTS (SELECT 1 FROM task_activity a "
            "                  WHERE a.source_event_id = 'history:' || h.id)"
        ).fetchone()[0]
        return pending == 0
    finally:
        conn.close()


def _status_body(new_value: str | None) -> str:
    verbs = {
        "active": "Started", "done": "Completed", "cancelled": "Cancelled",
        "waiting": "Waiting on input", "in_review": "Sent for review",
        "todo": "Moved to todo", "backlog": "Moved to backlog",
        "triage": "Moved to triage",
    }
    return verbs.get(new_value or "", f"Moved to {new_value}")


def up() -> bool:
    db = _work_db_path()
    if not db.exists():
        print("       work DB not present yet — adapter will ensure Phase 2 schema on first use")
        return True
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()

        if not _has_table(conn, "entity_history"):
            print(f"       Ensured task_activity on {db.name} (no history to backfill)")
            return True

        rows = conn.execute(
            "SELECT id, entity_id, field_name, old_value, new_value, actor, timestamp "
            "FROM entity_history WHERE entity_type = 'task' "
            "  AND field_name IN ('status','delegate','title','priority',"
            "                     'description','assigned_to','due_at') "
            "ORDER BY id ASC"
        ).fetchall()

        inserted = 0
        for hid, tid, field, old, new, actor, ts in rows:
            marker = f"history:{hid}"
            exists = conn.execute(
                "SELECT 1 FROM task_activity WHERE source_event_id = ?", (marker,)
            ).fetchone()
            if exists:
                continue
            if field == "status":
                kind, body = "status_changed", _status_body(new)
            elif field == "delegate":
                kind = "delegated"
                body = f"Delegated to {new}" if new else "Delegate cleared"
            else:
                kind, body = "edited", f"Edited {field}"
            conn.execute(
                "INSERT INTO task_activity "
                "(task_id, ts, actor, kind, body, data, source_event_id) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (tid, ts or "", actor or "operator", kind, body, marker),
            )
            inserted += 1
        conn.commit()
        print(f"       Ensured task_activity on {db.name}; backfilled {inserted} narrative rows")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
