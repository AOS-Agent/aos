"""Migrate work.yaml → qareen.db.

One-time (idempotent) migration that reads the existing work.yaml
and writes all tasks, projects, goals, inbox items, and threads
into the qareen.db SQLite database.

Usage:
    from qareen.ontology.adapters.migrate_work import migrate
    stats = migrate(
        yaml_path='~/.aos/work/work.yaml',
        db_path='~/.aos/data/qareen.db',
    )
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _norm_dt(val) -> str | None:
    """Normalize a date/datetime value to ISO8601 string."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    s = str(val).strip()
    if not s:
        return None
    # Already ISO datetime
    if "T" in s:
        return s
    # Date-only: YYYY-MM-DD → append time
    if len(s) == 10 and s[4] == "-":
        return s + "T00:00:00"
    return s


def _json_list(val) -> str | None:
    """Encode a list as a JSON string, or None if empty."""
    if not val:
        return None
    if isinstance(val, list):
        return json.dumps(val)
    return None


def migrate(
    yaml_path: str = "~/.aos/work/work.yaml",
    db_path: str = "~/.aos/data/qareen.db",
) -> dict[str, int]:
    """Migrate work.yaml into qareen.db.

    Returns a dict of entity counts migrated.
    Idempotent — uses INSERT OR REPLACE.
    """
    import yaml

    yaml_path = str(Path(yaml_path).expanduser())
    db_path = str(Path(db_path).expanduser())

    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f) or {}

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # OFF during migration for ordering flexibility
    conn.row_factory = sqlite3.Row

    stats = {
        "tasks": 0,
        "projects": 0,
        "goals": 0,
        "inbox": 0,
        "threads": 0,
        "handoffs": 0,
    }

    # ── 1. Projects (must come first — tasks reference them) ─────────

    for p in data.get("projects", []):
        conn.execute(
            "INSERT OR REPLACE INTO projects "
            "(id, title, description, status, path, goal, done_when, "
            " telegram_bot_key, telegram_chat_key, telegram_forum_topic, "
            " stages, current_stage, version, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                p["id"],
                p.get("title", p["id"]),
                p.get("description"),
                p.get("status", "active"),
                p.get("path"),
                p.get("goal"),
                p.get("done_when"),
                p.get("telegram_bot_key"),
                p.get("telegram_chat_key"),
                p.get("telegram_forum_topic"),
                _json_list(p.get("stages")),
                p.get("current_stage"),
                datetime.now().isoformat(),
            ),
        )
        stats["projects"] += 1

    # ── 2. Goals ─────────────────────────────────────────────────────

    for g in data.get("goals", []):
        conn.execute(
            "INSERT OR REPLACE INTO goals "
            "(id, title, weight, description, project_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                g["id"],
                g.get("title", g["id"]),
                int(g["weight"] * 100) if isinstance(g.get("weight"), float) else int(g.get("weight", 0)),
                g.get("description"),
                g.get("project_id"),
            ),
        )
        stats["goals"] += 1

        # Key results (if present)
        for kr in g.get("key_results", []):
            conn.execute(
                "INSERT INTO key_results (goal_id, title, progress, target) "
                "VALUES (?, ?, ?, ?)",
                (
                    g["id"],
                    kr.get("title", ""),
                    kr.get("progress", 0),
                    kr.get("target"),
                ),
            )

    # ── 3. Tasks ─────────────────────────────────────────────────────

    for t in data.get("tasks", []):
        task_id = t["id"]
        project_id = t.get("project")
        # In YAML: 'parent' key for subtasks. Map to parent_id.
        parent_id = t.get("parent")
        status = t.get("status", "todo")
        priority = int(t.get("priority", 3))
        tags = t.get("tags", [])
        description = t.get("description") or t.get("notes")

        conn.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, title, status, priority, project_id, description, "
            " assigned_to, created_by, created_at, started_at, "
            " completed_at, due_at, parent_id, pipeline, pipeline_stage, "
            " recurrence, tags, version, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                task_id,
                t.get("title", ""),
                status,
                priority,
                project_id,
                description,
                t.get("assigned_to"),
                t.get("created_by") or t.get("source"),
                _norm_dt(t.get("created")),
                _norm_dt(t.get("started")),
                _norm_dt(t.get("completed")),
                _norm_dt(t.get("due")),
                parent_id,
                t.get("pipeline"),
                t.get("pipeline_stage"),
                t.get("recurrence"),
                _json_list(tags) if tags else None,
                datetime.now().isoformat(),
            ),
        )
        stats["tasks"] += 1

        # Handoff
        ho = t.get("handoff")
        if ho:
            files = ho.get("files_touched", ho.get("files", []))
            decisions = ho.get("decisions", [])
            blockers = ho.get("blockers", [])

            conn.execute(
                "INSERT OR REPLACE INTO task_handoffs "
                "(task_id, state, next_step, files, decisions, blockers, "
                " session_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    ho.get("state", ""),
                    ho.get("next_step", ho.get("next", "")),
                    _json_list(files),
                    _json_list(decisions),
                    _json_list(blockers),
                    ho.get("session_id"),
                    _norm_dt(ho.get("updated") or ho.get("timestamp")),
                ),
            )
            stats["handoffs"] += 1

    # ── 4. Inbox ─────────────────────────────────────────────────────

    for item in data.get("inbox", []):
        conn.execute(
            "INSERT OR REPLACE INTO inbox "
            "(id, text, captured_at, project_id) "
            "VALUES (?, ?, ?, ?)",
            (
                item["id"],
                item.get("text", ""),
                _norm_dt(item.get("captured")),
                item.get("project_id") or item.get("project"),
            ),
        )
        stats["inbox"] += 1

    # ── 5. Threads ───────────────────────────────────────────────────

    for th in data.get("threads", []):
        conn.execute(
            "INSERT OR REPLACE INTO threads "
            "(id, title, status, created_at, project_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                th["id"],
                th.get("title", ""),
                th.get("status", "active"),
                _norm_dt(th.get("started") or th.get("created_at")),
                th.get("project_id") or th.get("project"),
            ),
        )
        stats["threads"] += 1

    # ── 6. FTS index ─────────────────────────────────────────────────

    _rebuild_fts(conn)

    # ── 7. Create links: task→project (belongs_to) ───────────────────

    # Link tasks to their projects
    rows = conn.execute(
        "SELECT id, project_id FROM tasks WHERE project_id IS NOT NULL"
    ).fetchall()
    now = datetime.now().isoformat()
    for row in rows:
        import uuid
        conn.execute(
            "INSERT OR REPLACE INTO links "
            "(id, link_type, from_type, from_id, to_type, to_id, "
            " direction, properties, created_at, created_by) "
            "VALUES (?, 'belongs_to', 'task', ?, 'project', ?, "
            " 'directed', NULL, ?, 'migration')",
            (str(uuid.uuid4()), row["id"], row["project_id"], now),
        )

    # Link subtasks to parents
    rows = conn.execute(
        "SELECT id, parent_id FROM tasks WHERE parent_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        import uuid
        conn.execute(
            "INSERT OR REPLACE INTO links "
            "(id, link_type, from_type, from_id, to_type, to_id, "
            " direction, properties, created_at, created_by) "
            "VALUES (?, 'subtask_of', 'task', ?, 'task', ?, "
            " 'directed', NULL, ?, 'migration')",
            (str(uuid.uuid4()), row["id"], row["parent_id"], now),
        )

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()

    return stats


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index from all tasks.

    If the FTS table is corrupted, drop all shadow tables and recreate.
    """
    # Try to clear — if corrupted, recreate from scratch
    try:
        conn.execute("DELETE FROM tasks_fts")
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        # FTS tables are corrupted — drop and recreate
        for t in ("tasks_fts", "tasks_fts_config", "tasks_fts_data",
                  "tasks_fts_docsize", "tasks_fts_idx"):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                pass
        conn.commit()
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5("
            "    title, description, content=tasks, content_rowid=rowid"
            ")"
        )
        conn.commit()

    # Re-populate from tasks table
    rows = conn.execute("SELECT rowid, title, description FROM tasks").fetchall()
    for row in rows:
        try:
            conn.execute(
                "INSERT INTO tasks_fts(rowid, title, description) VALUES(?, ?, ?)",
                (row["rowid"], row["title"], row["description"] or ""),
            )
        except (sqlite3.OperationalError, sqlite3.IntegrityError):
            pass


if __name__ == "__main__":
    stats = migrate()
    print(f"Migration complete: {stats}")
