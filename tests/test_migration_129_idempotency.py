"""Migration 129 — close the cwd-keyed auto threads — idempotency and safety.

Same contract as 111–128: up() runs twice without the second run moving
anything, never touches the live instance, and degrades gracefully when work.db
or the threads table is absent. HOME is redirected before the module is
imported, since WORK_DB is resolved from Path.home() at module scope.

The interesting assertions are the ones about what SURVIVES: a promoted thread,
a hand-written thread, and any thread that already knows its project — that last
one is the continuity the project-keyed generator is built on, so closing it
would make this migration fight the change it exists to support.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"


def load_migration(name: str, home: Path):
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


def _seed(db: Path, rows, with_project_col: bool = True) -> None:
    conn = sqlite3.connect(str(db))
    cols = "id TEXT PRIMARY KEY, title TEXT, status TEXT, created_at TEXT"
    if with_project_col:
        cols += ", project_id TEXT"
    cols += ", cwd TEXT"
    conn.execute(f"CREATE TABLE threads ({cols})")
    for r in rows:
        if with_project_col:
            conn.execute(
                "INSERT INTO threads (id, title, status, created_at, project_id, cwd) "
                "VALUES (?, ?, ?, ?, ?, ?)", r
            )
        else:
            conn.execute(
                "INSERT INTO threads (id, title, status, created_at, cwd) "
                "VALUES (?, ?, ?, ?, ?)", (r[0], r[1], r[2], r[3], r[5])
            )
    conn.commit()
    conn.close()


def _statuses(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT id, status FROM threads").fetchall())
    finally:
        conn.close()


MIXED = [
    # cwd-keyed auto threads — the target
    ("th1", "Work in v0.7.1-bdd0739", "exploring", "2026-09-12", None, "/h/aos-releases/v0.7.1-bdd0739"),
    ("th2", "Work in agent-a02accbf", "exploring", "2026-09-13", None, "/h/project/aos/.claude/worktrees/x"),
    # already closed by migration 115 — untouched
    ("th3", "Work in core", "closed", "2026-08-01", None, "/h/aos/core"),
    # promoted — untouched
    ("th4", "Blaxle — workspace app build", "promoted", "2026-08-20", "blaxle", None),
    # hand-written, still exploring — does not match the title shape
    ("th5", "People DB intelligence gaps", "exploring", "2026-07-01", None, None),
    # already project-keyed: the continuity the new generator reuses
    ("th6", "Work in aos", "exploring", "2026-09-13", "aos", "/h/project/aos"),
]


def test_129_closes_only_the_unreachable_rows_then_is_a_noop(home):
    db = home / ".aos" / "data" / "work.db"
    _seed(db, MIXED)

    m = load_migration("129", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    after = _statuses(db)
    assert after["th1"] == "closed"
    assert after["th2"] == "closed"
    assert after["th3"] == "closed"           # was already
    assert after["th4"] == "promoted"         # never touched
    assert after["th5"] == "exploring"        # hand-written survives
    assert after["th6"] == "exploring"        # project-keyed survives

    assert m.up() is True
    assert _statuses(db) == after


def test_129_makes_a_backup_before_writing(home):
    db = home / ".aos" / "data" / "work.db"
    _seed(db, MIXED)
    load_migration("129", home).up()

    backups = list((home / ".aos" / "backups" / "pre-purge").glob("work.db.bak-*"))
    assert len(backups) == 1, f"expected one pre-purge backup, found {backups}"


def test_129_is_a_noop_when_nothing_matches(home):
    db = home / ".aos" / "data" / "work.db"
    _seed(db, [MIXED[3], MIXED[4], MIXED[5]])

    m = load_migration("129", home)
    assert m.check() is True
    assert m.up() is True
    assert not (home / ".aos" / "backups" / "pre-purge").exists(), (
        "a no-op run must not copy the database"
    )


def test_129_is_a_noop_without_a_db(home):
    m = load_migration("129", home)
    assert m.check() is True
    assert m.up() is True


def test_129_is_a_noop_without_a_threads_table(home):
    db = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    m = load_migration("129", home)
    assert m.check() is True
    assert m.up() is True


def test_129_is_a_noop_without_a_project_id_column(home):
    """A threads table that predates project_id has nothing to key on, so the
    migration must stand down rather than close every auto thread it sees."""
    db = home / ".aos" / "data" / "work.db"
    _seed(db, MIXED, with_project_col=False)

    m = load_migration("129", home)
    assert m.check() is True
    assert m.up() is True
    assert _statuses(db)["th1"] == "exploring"


def test_live_instance_is_untouched_by_this_suite():
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
