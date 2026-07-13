"""Tests for the work DB resolution port (aos#130 phase 2, Qareen side).

Mirrors core/engine/work/backend.py's _resolve_db_path/_is_seeded_work_db
(kernel side, already shipped) — this is the Qareen-side port in
core/qareen/ontology/adapters/work.py, plus the session routing that keeps
sessions/session_tasks on qareen.db (Qareen-owned until aos#131) even after
tasks/projects/etc. move to the kernel-owned work.db.

Uses minimal hand-rolled DDL rather than schemas/qareen.sql: that file is
documented (core/infra/migrations/050_work_db_ownership.py) to drift from the
live, migration-patched schema, so it isn't a reliable fixture source.
"""

import sqlite3
import sys
from pathlib import Path

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.ontology.adapters.work import (  # noqa: E402
    WorkAdapter,
    _is_seeded_work_db,
    resolve_work_db_path,
)

TASKS_DDL = (
    "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
    "priority INTEGER, project_id TEXT, description TEXT, assigned_to TEXT, "
    "created_by TEXT, created_at TEXT, started_at TEXT, completed_at TEXT, "
    "due_at TEXT, parent_id TEXT, pipeline TEXT, pipeline_stage TEXT, "
    "recurrence TEXT, tags TEXT, version INTEGER, modified_at TEXT)"
)
TASK_HANDOFFS_DDL = (
    "CREATE TABLE task_handoffs (task_id TEXT PRIMARY KEY, state TEXT, "
    "next_step TEXT, files TEXT, decisions TEXT, blockers TEXT, "
    "session_id TEXT, timestamp TEXT)"
)
SESSIONS_DDL = (
    "CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT, started_at TEXT, "
    "ended_at TEXT, task_id TEXT, thread_id TEXT, outcome TEXT)"
)
SESSION_TASKS_DDL = (
    "CREATE TABLE session_tasks (session_id TEXT, task_id TEXT, relation TEXT)"
)


def _work_db(path: Path) -> Path:
    """A DB with just the work tables (post-cutover work.db shape)."""
    conn = sqlite3.connect(str(path))
    conn.execute(TASKS_DDL)
    conn.execute(TASK_HANDOFFS_DDL)
    conn.commit()
    conn.close()
    return path


def _qareen_db(path: Path) -> Path:
    """A DB with work + session tables (pre-cutover qareen.db shape)."""
    conn = sqlite3.connect(str(path))
    conn.execute(TASKS_DDL)
    conn.execute(TASK_HANDOFFS_DDL)
    conn.execute(SESSIONS_DDL)
    conn.execute(SESSION_TASKS_DDL)
    conn.commit()
    conn.close()
    return path


# ── resolve_work_db_path ─────────────────────────────────────────────────

def test_env_override_wins(tmp_path, monkeypatch):
    """AOS_WORK_DB, if set, always wins — even over a seeded work.db."""
    override = tmp_path / "custom.db"
    monkeypatch.setenv("AOS_WORK_DB", str(override))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_work_db_path() == override


def test_seeded_work_db_is_preferred(tmp_path, monkeypatch):
    """A properly seeded ~/.aos/data/work.db (has a tasks table) wins over qareen.db."""
    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    data_dir = tmp_path / ".aos" / "data"
    data_dir.mkdir(parents=True)
    work_db = _work_db(data_dir / "work.db")

    assert resolve_work_db_path() == work_db


def test_empty_work_db_falls_back_to_qareen(tmp_path, monkeypatch):
    """A 0-byte or half-initialized work.db must NOT trigger the cutover."""
    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    data_dir = tmp_path / ".aos" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "work.db").touch()  # 0 bytes — not seeded

    assert resolve_work_db_path() == data_dir / "qareen.db"


def test_missing_work_db_falls_back_to_qareen(tmp_path, monkeypatch):
    """No work.db at all (pre-migration machine) falls back to qareen.db."""
    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_work_db_path() == tmp_path / ".aos" / "data" / "qareen.db"


def test_is_seeded_work_db_false_for_missing_file(tmp_path):
    assert _is_seeded_work_db(tmp_path / "nope.db") is False


def test_is_seeded_work_db_false_for_table_less_db(tmp_path):
    """A valid sqlite file without a tasks table (e.g. wrong schema) is not seeded."""
    path = tmp_path / "other.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE unrelated (id TEXT)")
    conn.commit()
    conn.close()

    assert _is_seeded_work_db(path) is False


# ── Session routing (WorkAdapter._session_conn) ─────────────────────────

def test_session_enrichment_reads_from_same_conn_when_present(tmp_path):
    """Pre-cutover: session_tasks lives in the same DB as tasks — no fallback needed."""
    db = _qareen_db(tmp_path / "qareen.db")
    adapter = WorkAdapter(db_path=str(db))

    adapter._conn.execute(
        "INSERT INTO tasks (id, title, status, priority, created_at) "
        "VALUES ('t#1', 'Test task', 'todo', 3, '2026-01-01T00:00:00')"
    )
    adapter._conn.execute(
        "INSERT INTO sessions (id, status, started_at) "
        "VALUES ('s1', 'active', '2026-01-01T00:00:00')"
    )
    adapter._conn.execute(
        "INSERT INTO session_tasks (session_id, task_id, relation) "
        "VALUES ('s1', 't#1', 'worked_on')"
    )
    adapter._conn.commit()

    task = adapter.get("t#1")
    assert getattr(task, "sessions", None) == [{"id": "s1", "date": "2026-01-01"}]


def test_session_enrichment_falls_back_to_qareen_db(tmp_path, monkeypatch):
    """Post-cutover: work.db has tasks but no session_tasks — route to qareen.db."""
    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    data_dir = tmp_path / ".aos" / "data"
    data_dir.mkdir(parents=True)

    work_db = _work_db(data_dir / "work.db")

    qareen_db = _qareen_db(data_dir / "qareen.db")
    qconn = sqlite3.connect(str(qareen_db))
    qconn.execute(
        "INSERT INTO sessions (id, status, started_at) "
        "VALUES ('s1', 'active', '2026-01-01T00:00:00')"
    )
    qconn.execute(
        "INSERT INTO session_tasks (session_id, task_id, relation) "
        "VALUES ('s1', 't#1', 'worked_on')"
    )
    qconn.commit()
    qconn.close()

    adapter = WorkAdapter(db_path=str(work_db))
    adapter._conn.execute(
        "INSERT INTO tasks (id, title, status, priority, created_at) "
        "VALUES ('t#1', 'Test task', 'todo', 3, '2026-01-01T00:00:00')"
    )
    adapter._conn.commit()

    task = adapter.get("t#1")
    assert getattr(task, "sessions", None) == [{"id": "s1", "date": "2026-01-01"}]


def test_session_conn_never_escapes_injected_env(tmp_path, monkeypatch):
    """Under AOS_WORK_DB injection (tests/sandboxes), never fall back to the real
    instance qareen.db — _session_conn must return None instead."""
    work_db = tmp_path / "work.db"
    monkeypatch.setenv("AOS_WORK_DB", str(work_db))

    adapter = WorkAdapter(db_path=str(work_db))
    adapter._conn.execute(TASKS_DDL)
    adapter._conn.execute(TASK_HANDOFFS_DDL)
    adapter._conn.commit()

    assert adapter._session_conn() is None


def test_link_session_to_task_no_op_when_session_conn_unavailable(tmp_path, monkeypatch):
    """When _session_conn() returns None, link_session_to_task must not crash —
    it should still return the task, just without the session link recorded."""
    work_db = tmp_path / "work.db"
    monkeypatch.setenv("AOS_WORK_DB", str(work_db))

    adapter = WorkAdapter(db_path=str(work_db))
    adapter._conn.execute(TASKS_DDL)
    adapter._conn.execute(TASK_HANDOFFS_DDL)
    adapter._conn.execute(
        "INSERT INTO tasks (id, title, status, priority, created_at) "
        "VALUES ('t#1', 'Test task', 'todo', 3, '2026-01-01T00:00:00')"
    )
    adapter._conn.commit()

    result = adapter.link_session_to_task("t#1", "s1", outcome="done")
    assert result is not None
    assert result.id == "t#1"
    assert getattr(result, "sessions", None) in (None, [])


def test_link_session_to_task_writes_to_qareen_db_only(tmp_path, monkeypatch):
    """Post-cutover: linking a session must land in qareen.db, not work.db."""
    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    data_dir = tmp_path / ".aos" / "data"
    data_dir.mkdir(parents=True)

    work_db = _work_db(data_dir / "work.db")
    qareen_db = _qareen_db(data_dir / "qareen.db")

    adapter = WorkAdapter(db_path=str(work_db))
    adapter._conn.execute(
        "INSERT INTO tasks (id, title, status, priority, created_at) "
        "VALUES ('t#1', 'Test task', 'todo', 3, '2026-01-01T00:00:00')"
    )
    adapter._conn.commit()

    result = adapter.link_session_to_task("t#1", "s1", outcome="shipped")
    assert result is not None
    assert getattr(result, "sessions", None) == [{"id": "s1", "outcome": "shipped"}] \
        or getattr(result, "sessions", None)[0]["id"] == "s1"

    # work.db must NOT have gained a sessions/session_tasks table or rows.
    wconn = sqlite3.connect(str(work_db))
    tables = {
        r[0] for r in wconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "sessions" not in tables
    assert "session_tasks" not in tables
    wconn.close()

    # qareen.db must have the link.
    qconn = sqlite3.connect(str(qareen_db))
    link = qconn.execute(
        "SELECT * FROM session_tasks WHERE session_id = ? AND task_id = ?",
        ("s1", "t#1"),
    ).fetchone()
    assert link is not None
    qconn.close()
