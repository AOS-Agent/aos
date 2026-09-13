"""Migration 123 — sessions/session_tasks into work.db — idempotency and safety.

Same contract as 111–125: up() runs twice with the second run changing nothing,
never touches the live instance, and degrades gracefully when either database is
absent. HOME is redirected before the module is imported, since both DB paths are
resolved from Path.home() at module scope.

The synthetic pair of databases mirrors the live shape that matters, including
the parts that would break a naive copy:

  * `session_tasks` rows whose `session_id` is not in `sessions` (79 of these
     exist live) — the reason the destination tables carry no REFERENCES clause;
  * `sessions.task_id` pointing at a task work.db does not have (migration 114
     purged 368 of them);
  * rows already present in the destination, so the second run has something to
     collide with and must ignore rather than fail.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"

# Captured before any fixture patches it — the value a sandbox must not be.
_REAL_HOME = Path.home()

QAREEN_SCHEMA = """
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT,
    operator_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    project_id      TEXT,
    task_id         TEXT,
    thread_id       TEXT,
    outcome         TEXT,
    transcript_summary TEXT,
    utterance_count INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0
);
CREATE TABLE session_tasks (
    session_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    relation        TEXT NOT NULL,
    PRIMARY KEY (session_id, task_id)
);
-- Left behind on purpose: migration 108 keeps these in qareen.db.
CREATE TABLE loop_signals (id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE cron_runs (id INTEGER PRIMARY KEY, name TEXT);
"""

WORK_SCHEMA = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, project_id TEXT);
CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,
                      created_at TEXT, project_id TEXT, cwd TEXT);
"""


def load_migration(name: str, home: Path):
    assert Path.home() != _REAL_HOME, (
        "Path.home() still resolves to the operator's real home right before "
        "load_migration() was about to exec a migration module. The calling "
        "test's `home` fixture must patch Path.home() (persistently, for the "
        "whole test) before calling load_migration() — this migration resolves "
        f"its paths per call, so refusing to run {name!r} against the live "
        "instance is the only safe answer."
    )
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".aos" / "data").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


def _seed_pair(home: Path, *, qareen_sessions=6, already_in_work=0):
    """A synthetic qareen.db + work.db pair. Returns (work_db, qareen_db)."""
    work_db = home / ".aos" / "data" / "work.db"
    qareen_db = home / ".aos" / "data" / "qareen.db"

    wc = sqlite3.connect(str(work_db))
    wc.executescript(WORK_SCHEMA)
    wc.execute("INSERT INTO tasks (id, title) VALUES ('aos#1', 'A real task')")
    wc.commit()
    wc.close()

    qc = sqlite3.connect(str(qareen_db))
    qc.executescript(QAREEN_SCHEMA)
    for i in range(qareen_sessions):
        qc.execute(
            "INSERT INTO sessions (id, status, started_at, task_id) VALUES (?, ?, ?, ?)",
            (f"sess-{i}", "ended", f"2026-09-0{i % 9 + 1}", "aos#1" if i % 2 else "aos#404"),
        )
        qc.execute(
            "INSERT INTO session_tasks (session_id, task_id, relation) VALUES (?, ?, 'worked_on')",
            (f"sess-{i}", "aos#1"),
        )
    # An orphan link: session_id with no sessions row. 79 of these exist live.
    qc.execute(
        "INSERT INTO session_tasks (session_id, task_id, relation) "
        "VALUES ('sess-gone', 'aos#1', 'worked_on')"
    )
    qc.execute("INSERT INTO loop_signals (id, body) VALUES ('sig1', 'stays put')")
    qc.commit()
    qc.close()

    if already_in_work:
        # A partial earlier run: the tables exist in work.db with some rows.
        m = load_migration("123", home)
        wc = sqlite3.connect(str(work_db))
        wc.executescript(m.SCHEMA)
        for i in range(already_in_work):
            wc.execute(
                "INSERT INTO sessions (id, status, started_at) VALUES (?, 'ended', ?)",
                (f"sess-{i}", "2026-01-01"),
            )
        wc.commit()
        wc.close()

    return work_db, qareen_db


def _counts(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for t in ("sessions", "session_tasks"):
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out
    finally:
        conn.close()


def test_123_moves_both_tables_then_is_a_noop(home):
    work_db, qareen_db = _seed_pair(home)
    m = load_migration("123", home)

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    assert _counts(work_db) == _counts(qareen_db) == {"sessions": 6, "session_tasks": 7}

    before = _counts(work_db)
    assert m.up() is True
    assert _counts(work_db) == before
    assert m.check() is True


def test_123_copies_orphan_links_rather_than_rejecting_them(home):
    """The destination must not declare a foreign key the live data violates —
    79 session_tasks rows live reference a session id that is not in sessions."""
    work_db, _ = _seed_pair(home)
    load_migration("123", home).up()

    conn = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True)
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM session_tasks st WHERE NOT EXISTS "
            "(SELECT 1 FROM sessions s WHERE s.id = st.session_id)"
        ).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 1, "the orphan link must survive the move, not be dropped"


def test_123_completes_a_partial_earlier_run(home):
    work_db, qareen_db = _seed_pair(home, already_in_work=2)
    m = load_migration("123", home)
    assert m.check() is False
    assert m.up() is True
    assert _counts(work_db)["sessions"] == 6


def test_123_leaves_qareen_db_in_place_and_intact(home):
    """Nothing is deleted. qareen.db still owns intelligence, loop signals and
    cron_runs (migration 108) — it just stops being consulted for sessions."""
    _, qareen_db = _seed_pair(home)
    load_migration("123", home).up()

    assert qareen_db.exists()
    conn = sqlite3.connect(f"file:{qareen_db}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 6
        assert conn.execute("SELECT body FROM loop_signals").fetchone()[0] == "stays put"
    finally:
        conn.close()


def test_123_backs_up_both_databases_before_writing(home):
    _seed_pair(home)
    load_migration("123", home).up()

    backups = sorted(p.name.split(".bak-")[0]
                     for p in (home / ".aos" / "backups" / "pre-merge").iterdir())
    assert backups == ["qareen.db", "work.db"], (
        "both sides must be pinned — a half-finished merge is only recoverable "
        f"if the source was copied too; found {backups}"
    )


def test_123_creates_the_tables_when_there_is_nothing_to_move(home):
    """A machine with no qareen.db still needs the tables: the engine no longer
    falls back, so an absent `sessions` table is a crash, not a skipped join."""
    work_db = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(work_db))
    conn.executescript(WORK_SCHEMA)
    conn.commit()
    conn.close()

    m = load_migration("123", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True
    assert _counts(work_db) == {"sessions": 0, "session_tasks": 0}


def test_123_is_a_noop_without_a_work_db(home):
    m = load_migration("123", home)
    assert m.check() is True
    assert m.up() is True


# Captured at collection time, before any test can patch Path.home(): what the
# real instance's pre-merge backup dir held when the suite started. Since 0.7.7
# installed here, migration 123 HAS run for real, so the dir legitimately
# exists — the invariant is that this suite adds nothing to it, not that it is
# absent.
_PRE_MERGE_DIR = Path("~").expanduser() / ".aos" / "backups" / "pre-merge"
_PRE_MERGE_BEFORE = (
    sorted(q.name for q in _PRE_MERGE_DIR.iterdir()) if _PRE_MERGE_DIR.exists() else None
)


def test_live_instance_is_untouched_by_this_suite():
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
    after = sorted(q.name for q in _PRE_MERGE_DIR.iterdir()) if _PRE_MERGE_DIR.exists() else None
    assert after == _PRE_MERGE_BEFORE, (
        "the suite wrote a pre-merge backup into the real instance"
    )
