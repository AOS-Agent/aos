"""Migration 121 — threads.cwd column (aos#223) — idempotency and safety.

Same contract as 111-120: up() must be safe to run twice, must never touch
the live instance, and must degrade gracefully when work.db (or the threads
table) does not exist yet. HOME is redirected before the migration module is
imported, since it resolves WORK_DB = Path.home() / ".aos" / "data" /
"work.db" at module scope.
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


def _seed_threads_table(db: Path, with_cwd: bool = False) -> None:
    conn = sqlite3.connect(str(db))
    if with_cwd:
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
            "created_at TEXT, project_id TEXT, cwd TEXT)"
        )
    else:
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
            "created_at TEXT, project_id TEXT)"
        )
    conn.execute(
        "INSERT INTO threads (id, title, status, created_at) "
        "VALUES ('th1', 'Work in aos', 'exploring', '2026-09-01')"
    )
    conn.commit()
    conn.close()


def test_121_adds_column_then_is_a_noop(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_threads_table(db, with_cwd=False)

    m = load_migration("121", home)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(threads)")}
    assert "cwd" in cols

    # Second run must not error and must not duplicate the column.
    assert m.up() is True
    assert m.check() is True
    cols_after = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(threads)")}
    assert cols_after == cols


def test_121_preserves_existing_rows(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_threads_table(db, with_cwd=False)

    load_migration("121", home).up()

    row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
        "SELECT title, status, cwd FROM threads WHERE id = 'th1'"
    ).fetchone()
    assert row == ("Work in aos", "exploring", None)


def test_121_is_a_noop_when_column_already_present(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_threads_table(db, with_cwd=True)

    m = load_migration("121", home)
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_121_is_a_noop_without_a_db(home):
    m = load_migration("121", home)
    assert m.check() is True
    assert m.up() is True


def test_121_is_a_noop_without_a_threads_table(home):
    db = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    m = load_migration("121", home)
    assert m.check() is True
    assert m.up() is True


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard — see tests/test_migrations_108_117_idempotency.py for
    the incident this pattern exists to catch."""
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
