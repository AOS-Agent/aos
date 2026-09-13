"""One database: sessions and tasks live in the same file (aos#131 closed).

Until migration 123 the work engine read tasks from work.db and sessions from a
second SQLite file, routing per statement through
``WorkAdapter._session_conn()``. Every task↔session and thread↔session link was
a join across two files, matched on id strings, with no foreign key and no
transaction spanning both — and the first time aos#223 made the "thread already
exists" branch reachable, ``link_session_to_thread`` crashed the SessionEnd hook
with "no such table: sessions", because it was writing to a table the database
in front of it did not have.

What these tests pin is the part that is easy to regress quietly: the links now
round-trip, in one file, with the env-injected database and nothing else open.
A reintroduced fallback would make them pass against the operator's real store
instead of the sandbox, so the file count is asserted too.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _tables(db_path) -> set[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def test_resolution_has_no_second_store(monkeypatch):
    """With no override, both resolvers name work.db and stop."""
    import backend as eng

    from core.engine.work.ontology.work import resolve_work_db_path

    monkeypatch.delenv("AOS_WORK_DB", raising=False)
    expected = Path.home() / ".aos" / "data" / "work.db"
    assert eng._resolve_db_path() == expected
    assert resolve_work_db_path() == expected


def test_adapter_creates_the_session_tables_on_a_fresh_store(work_env):
    """A fresh install gets them from the adapter, not from the migration —
    the same contract migration 121 has with threads.cwd. Without this, an
    engine with no fallback raises "no such table: sessions" on first link."""
    eng = work_env["engine"]
    eng.get_all_tasks()  # force adapter construction
    assert {"sessions", "session_tasks"} <= _tables(work_env["db_path"])


def test_session_conn_is_the_work_connection(work_env):
    eng = work_env["engine"]
    adapter = eng._get_adapter()
    assert adapter._session_conn() is adapter._conn, (
        "sessions must be read and written on the work connection — a second "
        "connection is the split this release removed"
    )


def test_task_session_link_round_trips_in_one_file(populated_work_env):
    env = populated_work_env
    eng = env["engine"]
    task_id = env["t1"]["id"]

    eng.link_session_to_task(task_id, "sess-aaa", outcome="shipped part one")
    eng.link_session_to_task(task_id, "sess-bbb")

    sessions = eng.get_task(task_id).get("sessions") or []
    assert {s["id"] for s in sessions} == {"sess-aaa", "sess-bbb"}, (
        f"expected both session links to read back, got {sessions}"
    )
    assert any(s.get("outcome") == "shipped part one" for s in sessions)

    # And they are in the injected file, not somewhere in ~/.aos.
    conn = sqlite3.connect(f"file:{env['db_path']}?mode=ro", uri=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM session_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_linking_the_same_session_twice_is_idempotent(populated_work_env):
    env = populated_work_env
    eng = env["engine"]
    task_id = env["t2"]["id"]

    eng.link_session_to_task(task_id, "sess-same")
    eng.link_session_to_task(task_id, "sess-same", outcome="second pass")

    sessions = eng.get_task(task_id).get("sessions") or []
    assert len(sessions) == 1


def test_thread_session_link_counts_sessions(work_env):
    eng = work_env["engine"]
    thread = eng.add_thread("Qren cutover night")

    eng.link_session_to_thread(thread["id"], "sess-1")
    result = eng.link_session_to_thread(thread["id"], "sess-2")

    assert len(result.get("sessions") or []) == 2, (
        "the thread's session count is read from the sessions table; before "
        "the merge this path wrote to a table that was not there"
    )


def test_no_work_engine_module_reaches_for_a_second_store():
    """The grep, as a test. A reintroduced fallback is invisible in review and
    obvious here."""
    pkg = Path(__file__).resolve().parents[3] / "core" / "engine" / "work"
    offenders = []
    for py in sorted(pkg.rglob("*.py")):
        text = py.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if "qareen.db" in line:
                offenders.append(f"{py.name}:{lineno}")
    assert offenders == [], f"work engine still names a second database: {offenders}"


def test_injected_db_is_honoured_absolutely(work_env):
    """AOS_WORK_DB is what makes this suite safe. It must be the whole answer,
    not a preference the resolver can talk itself out of."""
    import backend as eng

    assert os.environ["AOS_WORK_DB"] == str(work_env["db_path"])
    assert eng._resolve_db_path() == work_env["db_path"]
