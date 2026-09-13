"""
Idempotency and safety tests for the v0.7.7 migrations (111–116).

The migration contract is that up() can run twice. The runner replays on any
machine whose recorded level is behind, a release can be activated and rolled
back and activated again, and an operator can run `aos migrate` by hand — so a
second run doing damage the first did not is a bug that surfaces on someone
else's machine, at 4am, on data nobody backed up twice.

Every test here runs against a **throwaway copy** of the real ~/.aos/data/work.db
in tmp_path, with HOME redirected. Nothing touches the live instance. Where the
real DB is absent (CI), the DB-backed tests build an equivalent fixture, so the
suite asserts the same behaviour either way rather than silently skipping.

The shape of each test is the same: run up() once, record the world, run up()
again, assert the world did not move — and separately assert that the first run
actually did the thing, because a migration that does nothing is trivially
idempotent and useless.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"
LIVE_WORK_DB = Path.home() / ".aos" / "data" / "work.db"

# Captured at module-collection time — before any fixture has ever patched
# Path.home() — so this is the one place in this file that can reliably name
# "the operator's real home" to check load_migration() against.
_REAL_HOME = Path.home()

FIXTURE_TITLES = (
    "Fix the login bug",
    "Deploy the bridge service",
    "Refactor the database connection pool",
    "Real task",
    "First task",
    "Second task",
)


# ── Harness ──────────────────────────────────────────────────────────────────


def load_migration(name: str, home: Path):
    """Import a migration with Path.home() already pointing at the sandbox.

    Migrations resolve their paths at import time (HOME = Path.home() at module
    scope), so the patch has to be in place before exec_module, and the module
    has to be re-imported per test rather than cached.

    Defensive: refuses to exec a migration at all unless Path.home() is
    already sandboxed — i.e. unless the caller's own `home` fixture applied
    its patch first. This is not decorative. A migration whose own frozen
    constants are correctly sandboxed can still leak: migrations 111/112 call
    into core/infra/lib/default_off.py, which re-resolves Path.home() on
    *every* call (that is its own fix for the same class of bug) rather than
    once at import — so if the patch below were the only one in effect, it
    would already be gone by the time check()/up() run, and disable_service()
    would write to whatever Path.home() has moved on to. On 2026-09-13 that
    was the operator's real ~/.aos/config/services.yaml.
    """
    assert Path.home() != _REAL_HOME, (
        "Path.home() still resolves to the operator's real home right before "
        "load_migration() was about to exec a migration module. The calling "
        "test's `home` fixture must patch Path.home() (persistently, for the "
        "whole test) before calling load_migration() — refusing to run "
        f"migration {name!r} against the live instance."
    )
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(REPO / "core" / "infra" / "lib"))
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the AOS tree symlinked in, and nothing else real."""
    h = tmp_path / "home"
    (h / ".aos" / "data").mkdir(parents=True)
    (h / ".aos" / "config").mkdir(parents=True)
    (h / "Library" / "LaunchAgents").mkdir(parents=True)
    (h / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


def _seed_fixture_db(path: Path) -> None:
    """Build a work.db equivalent to the polluted live one."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT,
            project_id TEXT, created_at TEXT, parent_id TEXT
        );
        CREATE TABLE task_activity (
            id INTEGER PRIMARY KEY, task_id TEXT,
            ts TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL
        );
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, title TEXT, status TEXT,
            created_at TEXT, project_id TEXT
        );
        """
    )
    rows = []
    for i, title in enumerate(FIXTURE_TITLES):
        for n in range(10):
            rows.append((f"t#{i}{n}", title, "todo", None, "2026-05-01", None))
    # A real task that shares a fixture title but was touched — must survive.
    rows.append(("t#900", "Fix the login bug", "todo", "aos", "2026-05-02", None))
    # A real task with a fixture title created OUTSIDE the window — must survive.
    rows.append(("t#901", "Real task", "todo", "aos", "2026-08-01", None))
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?)", rows)
    insert_activity(conn, "t#900")

    threads = [(f"th{i}", "Work in v0.7.6-abc", "exploring", "2026-05-01", None)
               for i in range(50)]
    threads.append(("th-real", "People DB intelligence gaps", "exploring", "2026-05-01", None))
    threads.append(("th-new", "Work in v0.7.7-xyz", "exploring", "2999-01-01", None))
    conn.executemany("INSERT INTO threads VALUES (?,?,?,?,?)", threads)
    conn.commit()
    conn.close()


@pytest.fixture
def work_db(home):
    """A throwaway copy of the live work.db, or an equivalent fixture."""
    dest = home / ".aos" / "data" / "work.db"
    if LIVE_WORK_DB.exists():
        shutil.copy2(LIVE_WORK_DB, dest)
    else:
        _seed_fixture_db(dest)
    return dest


def insert_activity(conn: sqlite3.Connection, task_id: str) -> None:
    """Add a task_activity row, whatever that table's NOT NULL columns are.

    The live schema has ts/actor/kind/body NOT NULL and has gained columns
    before. Hard-coding the column list makes this test a tripwire for
    unrelated schema changes; introspecting it tests what it means to test —
    that the task has been touched.
    """
    cols = conn.execute("PRAGMA table_info(task_activity)").fetchall()
    values = {"task_id": task_id}
    for _cid, name, _type, notnull, default, pk in cols:
        if name in values or pk or default is not None or not notnull:
            continue
        values[name] = "2026-05-01T00:00:00" if name == "ts" else "test"
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO task_activity ({names}) VALUES ({marks})",
                 tuple(values.values()))


def count(db: Path, sql: str, params=()) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ── 111: work-runner default off ─────────────────────────────────────────────


def test_111_disables_then_is_a_noop(home):
    m = load_migration("111", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    before = (home / ".aos" / "config" / "services.yaml").read_text()
    assert m.up() is True
    assert (home / ".aos" / "config" / "services.yaml").read_text() == before


def test_111_respects_an_explicit_opt_in(home):
    (home / ".aos" / "config" / "services.yaml").write_text(
        "enabled:\n  - work-runner\ndisabled: []\n"
    )
    m = load_migration("111", home)
    assert m.check() is True, "an opted-in service is already in its desired state"
    m.up()
    from default_off import disabled_services, enabled_services
    assert "work-runner" in enabled_services()
    assert "work-runner" not in disabled_services()


def test_111_preserves_other_disabled_entries(home):
    (home / ".aos" / "config" / "services.yaml").write_text(
        "disabled:\n  - n8n\n  - sana-watch\n"
    )
    m = load_migration("111", home)
    m.up()
    from default_off import disabled_services
    off = disabled_services()
    assert {"n8n", "sana-watch", "work-runner"} <= off


# ── 112: comms arms default off ──────────────────────────────────────────────


def test_112_disables_all_three_then_is_a_noop(home):
    m = load_migration("112", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    before = (home / ".aos" / "config" / "services.yaml").read_text()
    m.up()
    assert (home / ".aos" / "config" / "services.yaml").read_text() == before


def test_112_records_every_arm(home):
    load_migration("112", home).up()
    from default_off import disabled_services
    assert {"sentinel", "converse", "envoy"} <= disabled_services()


def test_112_leaves_an_opted_in_arm_alone(home):
    (home / ".aos" / "config" / "services.yaml").write_text(
        "enabled:\n  - sentinel\ndisabled: []\n"
    )
    load_migration("112", home).up()
    from default_off import disabled_services, enabled_services
    assert "sentinel" in enabled_services()
    assert "sentinel" not in disabled_services()
    assert {"converse", "envoy"} <= disabled_services()


# ── 113: stale DB backup purge ───────────────────────────────────────────────


def test_113_purges_named_files_and_keeps_a_copy(home):
    data = home / ".aos" / "data"
    for name in ("comms.db.bak-preconverse", "qareen.db.bak-2026-06-26-chief-dejunk"):
        (data / name).write_bytes(b"x" * 1024)
    m = load_migration("113", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    for name in ("comms.db.bak-preconverse", "qareen.db.bak-2026-06-26-chief-dejunk"):
        assert not (data / name).exists()
    copies = list((home / ".aos" / "backups" / "pre-purge").iterdir())
    assert len(copies) == 2

    m.up()  # second run
    assert len(list((home / ".aos" / "backups" / "pre-purge").iterdir())) == 2


def test_113_never_globs(home):
    """A *.bak-* sweep would eat the next operator's safety copy."""
    data = home / ".aos" / "data"
    (data / "comms.db.bak-preconverse").write_bytes(b"x")
    innocent = data / "work.db.bak-2026-08-20-before-something-risky"
    innocent.write_bytes(b"precious")
    load_migration("113", home).up()
    assert innocent.exists(), "only the three named files may ever be deleted"


def test_113_is_a_noop_when_nothing_is_present(home):
    m = load_migration("113", home)
    assert m.check() is True
    assert m.up() is True


# ── 114: test-fixture task purge ─────────────────────────────────────────────


def test_114_deletes_fixtures_then_is_a_noop(work_db, home):
    m = load_migration("114", home)
    before = count(work_db, "SELECT COUNT(*) FROM tasks")
    assert m.check() is False, "the copied DB should still hold fixture rows"

    assert m.up() is True
    assert m.check() is True
    after = count(work_db, "SELECT COUNT(*) FROM tasks")
    assert after < before, "the first run must actually delete something"

    assert m.up() is True
    assert count(work_db, "SELECT COUNT(*) FROM tasks") == after


def test_114_spares_a_touched_task_with_a_fixture_title(work_db, home):
    """Zero task_activity rows is one of the three required conditions."""
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) "
        "VALUES ('t#guard1', 'Fix the login bug', 'todo', '2026-05-01')"
    )
    insert_activity(conn, "t#guard1")
    conn.commit()
    conn.close()

    load_migration("114", home).up()
    assert count(work_db, "SELECT COUNT(*) FROM tasks WHERE id='t#guard1'") == 1


def test_114_spares_a_fixture_title_outside_the_window(work_db, home):
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) "
        "VALUES ('t#guard2', 'Real task', 'todo', '2026-08-19')"
    )
    conn.commit()
    conn.close()

    load_migration("114", home).up()
    assert count(work_db, "SELECT COUNT(*) FROM tasks WHERE id='t#guard2'") == 1


def test_114_spares_a_similar_but_different_title(work_db, home):
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) "
        "VALUES ('t#guard3', 'Fix the login bug in checkout', 'todo', '2026-05-01')"
    )
    conn.commit()
    conn.close()

    load_migration("114", home).up()
    assert count(work_db, "SELECT COUNT(*) FROM tasks WHERE id='t#guard3'") == 1


def test_114_backs_up_before_deleting(work_db, home):
    load_migration("114", home).up()
    backups = list((home / ".aos" / "backups" / "pre-purge").glob("work.db.bak-*"))
    assert backups, "work.db must be copied before any DELETE"


def test_114_is_a_noop_without_a_db(home):
    m = load_migration("114", home)
    assert m.check() is True
    assert m.up() is True


# ── 115: stale thread close ──────────────────────────────────────────────────


def test_115_closes_then_is_a_noop(work_db, home):
    m = load_migration("115", home)
    assert m.check() is False
    open_before = count(work_db, "SELECT COUNT(*) FROM threads WHERE status='exploring'")

    assert m.up() is True
    assert m.check() is True
    open_after = count(work_db, "SELECT COUNT(*) FROM threads WHERE status='exploring'")
    assert open_after < open_before

    assert m.up() is True
    assert count(work_db, "SELECT COUNT(*) FROM threads WHERE status='exploring'") == open_after


def test_115_spares_hand_written_threads(work_db, home):
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO threads (id, title, status, created_at) "
        "VALUES ('th-guard', 'A real exploration', 'exploring', '2020-01-01')"
    )
    conn.commit()
    conn.close()

    load_migration("115", home).up()
    status = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True).execute(
        "SELECT status FROM threads WHERE id='th-guard'"
    ).fetchone()[0]
    assert status == "exploring", "only auto-generated 'Work in %' threads may be closed"


def test_115_spares_recent_threads(work_db, home):
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO threads (id, title, status, created_at) "
        "VALUES ('th-today', 'Work in v0.7.7-now', 'exploring', date('now'))"
    )
    conn.commit()
    conn.close()

    load_migration("115", home).up()
    status = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True).execute(
        "SELECT status FROM threads WHERE id='th-today'"
    ).fetchone()[0]
    assert status == "exploring", "the current working week stays open"


def test_115_closes_rather_than_deletes(work_db, home):
    total_before = count(work_db, "SELECT COUNT(*) FROM threads")
    load_migration("115", home).up()
    assert count(work_db, "SELECT COUNT(*) FROM threads") == total_before


def test_115_handles_both_date_formats(work_db, home):
    """created_at holds bare dates AND full ISO timestamps."""
    conn = sqlite3.connect(str(work_db))
    conn.execute(
        "INSERT INTO threads (id, title, status, created_at) "
        "VALUES ('th-iso', 'Work in old-iso', 'exploring', '2026-03-21T00:00:00')"
    )
    conn.commit()
    conn.close()

    load_migration("115", home).up()
    status = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True).execute(
        "SELECT status FROM threads WHERE id='th-iso'"
    ).fetchone()[0]
    assert status == "closed", "an ISO timestamp must compare as old, not be skipped"


# ── 116: Qren readiness ──────────────────────────────────────────────────────


def test_116_writes_report_then_is_a_noop(home):
    m = load_migration("116", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    report = home / ".aos" / "data" / "qren-readiness.json"
    assert report.exists()
    assert m.up() is True
    assert m.check() is True


def test_116_report_has_the_expected_shape(home):
    m = load_migration("116", home)
    m.up()
    import json
    data = json.loads((home / ".aos" / "data" / "qren-readiness.json").read_text())
    for key in ("schema_version", "machine", "aos", "engines", "services",
                "modules", "data", "qren"):
        assert key in data, key


def test_116_invite_token_stays_empty(home):
    load_migration("116", home).up()
    import json
    data = json.loads((home / ".aos" / "data" / "qren-readiness.json").read_text())
    assert data["qren"]["invite_token"] == ""


def test_116_never_claims_an_unprobed_login_state(home):
    """authenticated is null (not determined), never false (checked and no)."""
    load_migration("116", home).up()
    import json
    data = json.loads((home / ".aos" / "data" / "qren-readiness.json").read_text())
    for engine in data["engines"].values():
        assert engine["authenticated"] is None


# ── The live instance is never touched ───────────────────────────────────────


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard: these tests must never write to the real ~/.aos.

    This is not paranoia. The first version of default_off.py resolved
    Path.home() at import time, which means whichever caller imported it first
    decided — for the whole process — whose services.yaml every later caller
    wrote to. In a test run that is the operator's own machine.
    """
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"

    services = Path.home() / ".aos" / "config" / "services.yaml"
    if services.exists():
        # A *key*, not the substring. The framework's own header comment
        # (default_off.py `_HEADER`) explains `enabled:` in prose, and a real
        # migration run writes that header to this file — so a substring test
        # fails on any machine where 111/112 were legitimately applied,
        # including the operator's own after this release installs.
        import yaml
        data = yaml.safe_load(services.read_text()) or {}
        assert "enabled" not in data, (
            "the live services.yaml gained an `enabled:` key — a sandboxed "
            "migration wrote to the real instance"
        )
