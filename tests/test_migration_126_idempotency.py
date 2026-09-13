"""Migration 126 — inbox.fingerprint/count/last_seen columns (aos#239) —
idempotency and safety.

Same contract as 111-121: up() must be safe to run twice, must never touch
the live instance, and must degrade gracefully when work.db (or the inbox
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

# Captured before any fixture patches it — the value a sandbox must not be.
_REAL_HOME = Path.home()


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


def _seed_inbox_table(db: Path, with_new_cols: bool = False) -> None:
    conn = sqlite3.connect(str(db))
    if with_new_cols:
        conn.execute(
            "CREATE TABLE inbox (id TEXT PRIMARY KEY, text TEXT, captured_at TEXT, "
            "source TEXT, snoozed_until TEXT, fingerprint TEXT, "
            "count INTEGER DEFAULT 1, last_seen TEXT)"
        )
    else:
        conn.execute(
            "CREATE TABLE inbox (id TEXT PRIMARY KEY, text TEXT, captured_at TEXT, "
            "source TEXT, snoozed_until TEXT)"
        )
    conn.execute(
        "INSERT INTO inbox (id, text, captured_at, source) "
        "VALUES ('i1', 'a captured item', '2026-09-01T00:00:00', 'manual')"
    )
    conn.commit()
    conn.close()


def test_126_adds_columns_then_is_a_noop(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_inbox_table(db, with_new_cols=False)

    m = load_migration("126", home)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(inbox)")}
    assert {"fingerprint", "count", "last_seen"} <= cols

    # Second run must not error and must not duplicate any column.
    assert m.up() is True
    assert m.check() is True
    cols_after = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(inbox)")}
    assert cols_after == cols


def test_126_preserves_existing_rows(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_inbox_table(db, with_new_cols=False)

    load_migration("126", home).up()

    row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
        "SELECT text, source, fingerprint, count, last_seen FROM inbox WHERE id = 'i1'"
    ).fetchone()
    assert row[0] == "a captured item"
    assert row[1] == "manual"
    assert row[2] is None  # fingerprint: nothing to backfill for a pre-existing capture
    assert row[4] is None  # last_seen: same


def test_126_is_a_noop_when_columns_already_present(home):
    db = home / ".aos" / "data" / "work.db"
    _seed_inbox_table(db, with_new_cols=True)

    m = load_migration("126", home)
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_126_adds_only_the_missing_columns(home):
    """A partially-patched inbox (e.g. fingerprint added by hand) only gets
    the columns it is still missing."""
    db = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE inbox (id TEXT PRIMARY KEY, text TEXT, captured_at TEXT, "
        "source TEXT, fingerprint TEXT)"
    )
    conn.commit()
    conn.close()

    m = load_migration("126", home)
    assert m.check() is False
    assert m.up() is True

    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(inbox)")}
    assert {"fingerprint", "count", "last_seen"} <= cols
    assert m.check() is True


def test_126_is_a_noop_without_a_db(home):
    m = load_migration("126", home)
    assert m.check() is True
    assert m.up() is True


def test_126_is_a_noop_without_an_inbox_table(home):
    db = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    m = load_migration("126", home)
    assert m.check() is True
    assert m.up() is True


def test_126_reports_a_description_and_refuses_to_reverse(home):
    m = load_migration("126", home)
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION
    assert m.down() is False


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard — see tests/test_migrations_108_117_idempotency.py for
    the incident this pattern exists to catch."""
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
