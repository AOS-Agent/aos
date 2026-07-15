"""
Test suite for the AOS Context Injection Hook (core/engine/work/inject_context.py).

Tests exercise the module's output contract:
  - Always produces valid JSON
  - Contains the "additionalContext" key
  - Handles an empty or absent work database without crashing
  - Active tasks appear in context
  - High-priority tasks (P1/P2) are surfaced

Strategy: inject_context reads work/task state through the work backend
(``import backend as engine``), which opens a SQLite database. The backend
resolves that database from the ``AOS_WORK_DB`` environment variable first
(see backend._resolve_db_path and migration 050), so each test seeds a tiny
scratch work.db in tmp_path and points the hook at it via AOS_WORK_DB. The
hook runs as a real subprocess (stdin JSON in, stdout JSON out) so we exercise
the full pipeline without polluting this process's module state or touching the
operator's real ~/.aos/data database.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

WORK_PKG = Path(__file__).parent.parent / "core" / "engine" / "work"
INJECT_CONTEXT = WORK_PKG / "inject_context.py"


# ---------------------------------------------------------------------------
# Scratch work.db — the subset of the canonical schema the hook's read path
# touches (get_all_tasks, summary(), find_tasks_by_project_or_cwd). Mirrors
# core/qareen/schemas/qareen.sql for these tables, including the migration-added
# columns on `tasks` that the live database carries.
# ---------------------------------------------------------------------------

WORK_SCHEMA = """
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    path            TEXT,
    goal            TEXT,
    done_when       TEXT,
    short_id        TEXT,
    telegram_bot_key     TEXT,
    telegram_chat_key    TEXT,
    telegram_forum_topic INTEGER,
    stages          TEXT,
    current_stage   TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT
);

CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'todo',
    priority        INTEGER NOT NULL DEFAULT 3,
    project_id      TEXT REFERENCES projects(id),
    description     TEXT,
    assigned_to     TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    due_at          TEXT,
    parent_id       TEXT REFERENCES tasks(id),
    pipeline        TEXT,
    pipeline_stage  TEXT,
    recurrence      TEXT,
    tags            TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT,
    scheduled_at    TEXT,
    snoozed_until   TEXT,
    estimate_minutes INTEGER,
    story_points    REAL,
    actual_minutes  INTEGER,
    energy          TEXT,
    context         TEXT,
    area_id         TEXT,
    assignee_type   TEXT DEFAULT 'operator',
    recurrence_type TEXT DEFAULT 'fixed',
    template_id     TEXT,
    recurrence_index INTEGER
);

CREATE TABLE task_handoffs (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    state           TEXT NOT NULL,
    next_step       TEXT NOT NULL,
    files           TEXT,
    decisions       TEXT,
    blockers        TEXT,
    session_id      TEXT,
    timestamp       TEXT
);

CREATE TABLE goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    weight          INTEGER DEFAULT 0,
    description     TEXT,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE key_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals(id),
    title           TEXT NOT NULL,
    progress        INTEGER DEFAULT 0,
    target          TEXT
);

CREATE TABLE inbox (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE threads (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'active',
    created_at      TEXT,
    project_id      TEXT REFERENCES projects(id)
);
"""


def _make_work_db(db_path: Path, tasks=None, projects=None):
    """Create a scratch work.db and seed it with tasks/projects.

    Referenced projects are auto-created so project scoping renders, but the
    foreign key is not enforced (the connection leaves foreign_keys off), so
    tests need only specify the fields they assert on.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(WORK_SCHEMA)

        seeded_projects = set()

        def _ensure_project(pid: str):
            if pid and pid not in seeded_projects:
                conn.execute(
                    "INSERT INTO projects (id, title) VALUES (?, ?)",
                    (pid, pid.upper()),
                )
                seeded_projects.add(pid)

        for pid in projects or []:
            _ensure_project(pid)

        for t in tasks or []:
            pid = t.get("project")
            _ensure_project(pid)
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, project_id, "
                "created_at, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"],
                    t["title"],
                    t.get("status", "todo"),
                    t.get("priority", 3),
                    pid,
                    t.get("created", "2026-01-01"),
                    json.dumps(t["tags"]) if t.get("tags") else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def run_inject_context(db_path: Path, hook_input: dict = None) -> dict:
    """Run inject_context.py as a subprocess against db_path, return parsed JSON."""
    if hook_input is None:
        hook_input = {"session_id": "test-session-001", "cwd": str(db_path.parent)}

    import os
    env = dict(os.environ)
    env["AOS_WORK_DB"] = str(db_path)

    result = subprocess.run(
        [sys.executable, str(INJECT_CONTEXT)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        f"inject_context hook exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    output_line = result.stdout.strip()
    assert output_line, f"inject_context produced no output.\nstderr: {result.stderr}"

    return json.loads(output_line)


# ===========================================================================
# Output Format — 3 tests
# ===========================================================================

class TestOutputFormat:

    def test_output_is_valid_json_with_additionalcontext_key(self, tmp_path):
        """inject_context always outputs valid JSON with an 'additionalContext' key."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)

        output = run_inject_context(db_path)

        assert isinstance(output, dict), \
            "Output must be a JSON object (dict)"
        assert "additionalContext" in output, \
            f"Output must contain 'additionalContext' key, got keys: {list(output.keys())}"
        assert isinstance(output["additionalContext"], str), \
            "additionalContext must be a string"

    def test_works_with_no_work_db(self, tmp_path):
        """inject_context exits cleanly even when the work database does not exist."""
        db_path = tmp_path / "does_not_exist.db"
        # No database created — the hook must not crash.

        output = run_inject_context(db_path)

        # Must be valid JSON — key may or may not be present when there's no DB.
        assert isinstance(output, dict), \
            "Output must be a JSON object even with no work database"

    def test_works_with_tasks_but_none_active(self, tmp_path):
        """inject_context handles a work.db with only todo tasks (none active)."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Backlog task", "status": "todo", "priority": 3,
             "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)

        assert "additionalContext" in output, \
            "Must produce additionalContext when tasks exist but none are active"
        # Should not crash trying to list active tasks when there are none
        context = output["additionalContext"]
        assert isinstance(context, str) and len(context) > 0, \
            "Context string must be non-empty"


# ===========================================================================
# Content — 2 tests
# ===========================================================================

class TestContextContent:

    def test_active_tasks_appear_in_context(self, tmp_path):
        """Tasks with status='active' are surfaced in the injected context."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "aos#7", "title": "Active feature work", "status": "active",
             "priority": 2, "project": "aos", "created": "2026-01-01"},
            {"id": "aos#8", "title": "Idle backlog item", "status": "todo",
             "priority": 4, "project": "aos", "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)
        context = output.get("additionalContext", "")

        assert "Active feature work" in context, \
            "Active task title must appear in injected context"

    def test_high_priority_tasks_are_highlighted(self, tmp_path):
        """Priority 1 and 2 todo tasks appear under a 'High priority' section."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Urgent P1 task", "status": "todo",
             "priority": 1, "created": "2026-01-01"},
            {"id": "t#2", "title": "Important P2 task", "status": "todo",
             "priority": 2, "created": "2026-01-01"},
            {"id": "t#3", "title": "Normal priority task", "status": "todo",
             "priority": 3, "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)
        context = output.get("additionalContext", "")

        assert "Urgent P1 task" in context, \
            "P1 task must appear in context"
        assert "Important P2 task" in context, \
            "P2 task must appear in context"
        assert "High priority" in context, \
            "Context must contain a 'High priority' heading section"
