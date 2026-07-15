"""Regression tests for `--project <short-id>` resolution in the work backend.

Background — the bug being guarded against:
  `tasks.project_id` is a foreign key into `projects(id)`. A project created
  with `--short-id dod` is stored with the canonical id `p1`; the short-id is
  a separate, user-facing handle. When a task is added with `--project dod`,
  the backend must resolve `dod` -> the canonical id `p1` BEFORE setting the
  FK. Previously it stored the literal `"dod"` as `project_id`, which has no
  matching parent row and raised:

      sqlite3.IntegrityError: FOREIGN KEY constraint failed

Contract under test:
  - Creating a project with a custom short-id round-trips (short_id readable).
  - Adding a task scoped by that short-id succeeds (no FK violation).
  - The task's stored project_id equals the project's CANONICAL id.
  - The task's scoped id uses the short-id as its prefix (e.g. dod#1).
  - Projects whose id == their slug (e.g. "aos") keep working unchanged.
  - A task scoped by the canonical id (p1) also resolves correctly.

These tests use an isolated scratch qareen.db in tmp_path; they never touch
the operator's real ~/.aos/data/qareen.db.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_DIR = REPO_ROOT / "core" / "engine" / "work"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(WORK_DIR))

import backend  # noqa: E402

# ── Scratch DB ───────────────────────────────────────────────────────

# The subset of qareen.sql the work backend touches. Mirrors the canonical
# schema (core/qareen/schemas/qareen.sql) for these tables, including the
# short_id column on projects that this fix introduces.
SCRATCH_SCHEMA = """
PRAGMA foreign_keys=ON;

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
    modified_at     TEXT
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

-- Plain (non-external-content) FTS5: production uses content=tasks, but an
-- external-content table corrupts when _sync_fts issues its delete-before-
-- insert against a row that was never previously indexed (only possible on a
-- pristine DB). FTS search is incidental to these tests, so a plain FTS5 table
-- is used; _sync_fts's delete then degrades to a caught OperationalError.
CREATE VIRTUAL TABLE tasks_fts USING fts5(title, description);
"""


@pytest.fixture()
def work_db(tmp_path, monkeypatch):
    """Isolated scratch qareen.db wired into the work backend.

    Resets backend's lazy singletons so each test gets a fresh adapter
    bound to the scratch DB, and disables the best-effort side-effects
    (activity log / Qareen notify / GitHub) so tests stay hermetic.
    """
    db_path = tmp_path / "qareen.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCRATCH_SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(backend, "DB_PATH", db_path)
    # Reset lazy singletons so the adapter rebinds to the scratch DB.
    monkeypatch.setattr(backend, "_adapter", None)
    monkeypatch.setattr(backend, "_resolver", None)
    monkeypatch.setattr(backend, "_project_ctx", None)
    # Best-effort side effects must not reach the network or real files.
    monkeypatch.setattr(backend, "_on_task_created", lambda *a, **k: None)
    monkeypatch.setattr(backend, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(backend, "_notify_dashboard", lambda *a, **k: None)

    def _project_id_of(name: str) -> str | None:
        c = sqlite3.connect(str(db_path))
        try:
            row = c.execute(
                "SELECT project_id FROM tasks WHERE id = ?", (name,)
            ).fetchone()
            return row[0] if row else None
        finally:
            c.close()

    return {"db_path": db_path, "task_project_id": _project_id_of}


# ── Tests ────────────────────────────────────────────────────────────

def test_add_task_by_short_id_resolves_to_canonical_project(work_db):
    """The core regression: --project <short-id> must not raise an FK error
    and must store the canonical project id as the FK."""
    proj = backend.add_project("Deen Over Dunya", short_id="dod")
    assert proj["id"] == "p1", "Project should get the canonical auto id 'p1'"

    # This previously raised sqlite3.IntegrityError: FOREIGN KEY constraint failed
    task = backend.add_task("Build prayer tracker", project="dod")

    # FK is set to the CANONICAL project id, not the literal short-id.
    assert work_db["task_project_id"](task["id"]) == "p1", (
        "tasks.project_id must equal the canonical projects.id (p1), "
        f"got: {work_db['task_project_id'](task['id'])!r}"
    )
    # Task id uses the short-id as its scoped prefix.
    assert task["id"].startswith("dod#"), (
        f"Task scoped id should use the short-id prefix, got: {task['id']!r}"
    )


def test_short_id_round_trips_on_project(work_db):
    """A project's short_id is readable back after creation."""
    backend.add_project("Deen Over Dunya", short_id="dod")
    projects = {p["id"]: p for p in backend.get_all_projects()}
    assert projects["p1"].get("short_id") == "dod", (
        "short_id must round-trip through storage"
    )


def test_add_task_by_canonical_id_still_works(work_db):
    """Scoping a task by the canonical project id resolves correctly too."""
    backend.add_project("Deen Over Dunya", short_id="dod")
    task = backend.add_task("Scoped by canonical id", project="p1")
    assert work_db["task_project_id"](task["id"]) == "p1"


def test_project_id_equals_slug_unchanged(work_db):
    """Projects whose id == their slug (e.g. 'aos') keep working: the FK and
    the scoped prefix are both the slug."""
    backend.add_project("AOS Framework", short_id="aos", project_id="aos")
    task = backend.add_task("Implement SSE push", project="aos")
    assert work_db["task_project_id"](task["id"]) == "aos"
    assert task["id"].startswith("aos#"), (
        f"Task should be scoped aos#N, got: {task['id']!r}"
    )


def test_unaffiliated_task_has_no_project(work_db):
    """A task with no project stays unaffiliated (t# prefix, null FK)."""
    task = backend.add_task("No project here")
    assert work_db["task_project_id"](task["id"]) is None
    assert task["id"].startswith("t#"), (
        f"Unaffiliated task should use the 't#' prefix, got: {task['id']!r}"
    )


def test_pre_migration_db_without_short_id_column_degrades(tmp_path, monkeypatch):
    """On a DB that predates migration 046 (no projects.short_id column), the
    adapter must not crash: short_id resolution falls back to id-only and tasks
    scoped by the canonical project id still work."""
    db_path = tmp_path / "qareen.db"
    conn = sqlite3.connect(str(db_path))
    # Same scratch schema but with the short_id column removed from projects.
    schema = SCRATCH_SCHEMA.replace("    short_id        TEXT,\n", "")
    conn.executescript(schema)
    conn.commit()
    conn.close()

    monkeypatch.setattr(backend, "DB_PATH", db_path)
    monkeypatch.setattr(backend, "_adapter", None)
    monkeypatch.setattr(backend, "_resolver", None)
    monkeypatch.setattr(backend, "_project_ctx", None)
    monkeypatch.setattr(backend, "_on_task_created", lambda *a, **k: None)
    monkeypatch.setattr(backend, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(backend, "_notify_dashboard", lambda *a, **k: None)

    # short_id arg is silently dropped (no column), but project creation and
    # id==slug task scoping must still succeed without raising.
    backend.add_project("AOS Framework", short_id="aos", project_id="aos")
    task = backend.add_task("Implement SSE push", project="aos")
    assert task["id"].startswith("aos#")

    c = sqlite3.connect(str(db_path))
    try:
        row = c.execute(
            "SELECT project_id FROM tasks WHERE id = ?", (task["id"],)
        ).fetchone()
        assert row[0] == "aos"
    finally:
        c.close()


def test_move_task_to_short_id_resolves_to_canonical(work_db):
    """`work move <task> --to <short-id>` must resolve the FK to the canonical
    project id and re-scope the task under the short-id prefix."""
    backend.add_project("Deen Over Dunya", short_id="dod")
    task = backend.add_task("Wandering task")  # unaffiliated t#1

    moved = backend.move_tasks_to_project([task["id"]], "dod")
    new_id = moved[0]["new_id"]

    assert new_id.startswith("dod#"), (
        f"Moved task should be re-scoped under the short-id, got: {new_id!r}"
    )
    assert work_db["task_project_id"](new_id) == "p1", (
        "Moved task's project_id must be the canonical id, not the short-id"
    )
