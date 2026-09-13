"""Migration 124 — bridge conversation store (aos#235) — idempotency and safety.

Same contract as 111-121: up() must be safe to run twice, must never touch the
live instance, and must degrade gracefully when the file or the table is not
what it expects. HOME is redirected before the migration module is imported,
since it resolves BRIDGE_DB = Path.home() / ".aos" / "data" / "bridge.db" at
module scope.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"

EXPECTED_COLUMNS = {
    "id", "ts", "direction", "chat_id", "topic", "kind", "text_redacted",
    "meta_json", "status",
}


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


def _cols(db: Path) -> set:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()


def test_124_creates_the_store_then_is_a_noop(home):
    db = home / ".aos" / "data" / "bridge.db"
    m = load_migration("124", home)

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True
    assert EXPECTED_COLUMNS <= _cols(db)

    # Second run must not error and must not change the schema.
    before = _cols(db)
    assert m.up() is True
    assert m.check() is True
    assert _cols(db) == before


def test_124_creates_the_data_dir_if_missing(tmp_path, monkeypatch):
    h = tmp_path / "bare-home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    m = load_migration("124", h)
    assert m.up() is True
    assert (h / ".aos" / "data" / "bridge.db").exists()


def test_124_preserves_existing_messages(home):
    db = home / ".aos" / "data" / "bridge.db"
    load_migration("124", home).up()

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages (ts, direction, chat_id, topic, kind, text_redacted) "
        "VALUES ('2026-09-13T10:00:00', 'in', 42, 'dm', 'message', 'hello')"
    )
    conn.commit()
    conn.close()

    assert load_migration("124", home).up() is True

    row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
        "SELECT direction, text_redacted, status FROM messages").fetchone()
    assert row == ("in", "hello", "sent")


def test_124_adds_status_to_a_prerelease_store(home):
    """A store written before the quiet-hours queue existed has no status."""
    db = home / ".aos" / "data" / "bridge.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "direction TEXT NOT NULL, chat_id INTEGER, topic TEXT, kind TEXT, "
        "text_redacted TEXT, meta_json TEXT)"
    )
    conn.execute(
        "INSERT INTO messages (ts, direction, text_redacted) "
        "VALUES ('2026-09-01T09:00:00', 'out', 'an older row')"
    )
    conn.commit()
    conn.close()

    m = load_migration("124", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    row = sqlite3.connect(str(db)).execute(
        "SELECT text_redacted, status FROM messages").fetchone()
    assert row == ("an older row", "sent")


def test_124_leaves_the_dead_activity_db_alone(home):
    """Five-month-old dashboard rows are not conversation history."""
    dashboard = home / ".aos" / "data" / "dashboard"
    dashboard.mkdir(parents=True)
    activity = dashboard / "activity.db"
    conn = sqlite3.connect(str(activity))
    conn.execute("CREATE TABLE activity (id INTEGER PRIMARY KEY, agent TEXT)")
    conn.execute("INSERT INTO activity (agent) VALUES ('telegram')")
    conn.commit()
    conn.close()
    before = activity.read_bytes()

    assert load_migration("124", home).up() is True
    assert activity.read_bytes() == before


def test_124_declines_to_roll_back(home):
    assert load_migration("124", home).down() is False


def test_the_store_module_and_the_migration_agree_on_the_schema():
    """Two copies of a schema is one copy too many — keep them in lockstep."""
    store = (REPO / "core" / "services" / "bridge" / "conversation_store.py").read_text()
    migration = (MIGRATIONS / "124_bridge_conversation_store.py").read_text()
    for column in sorted(EXPECTED_COLUMNS):
        assert column in store, f"{column} missing from conversation_store.py"
        assert column in migration, f"{column} missing from migration 124"


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard — see tests/test_migrations_108_117_idempotency.py for
    the incident this pattern exists to catch."""
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
