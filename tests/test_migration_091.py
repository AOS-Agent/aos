"""Migration 091 — one-shot import of islah bugs.yaml → work.db.

Drives the migration end-to-end against a FAKE ledger fixture and an isolated
work.db (the live migration-patched schema from tests/fixtures/work_schema.sql).
Verifies: check() is False before / True after, up() imports every bug, and a
re-run is a clean no-op (check() stays True, no duplicate tasks).

All paths redirected to tmp_path; nothing touches the operator's real bugs.yaml
or work.db.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = _ROOT / "core" / "infra" / "migrations"
_FIXTURE = _ROOT / "tests" / "fixtures" / "islah_bugs_fake.yaml"
_SCHEMA = (_ROOT / "tests" / "fixtures" / "work_schema.sql").read_text()


def _load(name: str):
    path = MIGRATIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mig(tmp_path, monkeypatch):
    db = tmp_path / "work.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setenv("AOS_WORK_DB", str(db))
    m = _load("091_islah_bugs_import")
    monkeypatch.setattr(m, "_BUGS_YAML", _FIXTURE)

    # Reset any cached backend singleton so it binds to this isolated DB.
    try:
        import backend as b
        monkeypatch.setattr(b, "_adapter", None)
        monkeypatch.setattr(b, "_resolver", None)
        monkeypatch.setattr(b, "_project_ctx", None)
    except Exception:
        pass

    yield {"m": m, "db": db}
    sys.modules.pop("091_islah_bugs_import", None)


def test_check_false_then_up_imports_then_check_true(mig):
    m, db = mig["m"], mig["db"]

    assert m.check() is False        # ledger present, nothing imported yet
    assert m.up() is True
    assert m.check() is True         # every bug now has its created beat

    conn = sqlite3.connect(str(db))
    try:
        n_tasks = conn.execute(
            "SELECT count(*) FROM tasks WHERE pipeline = 'bug'"
        ).fetchone()[0]
        n_created = conn.execute(
            "SELECT count(*) FROM task_activity WHERE kind = 'created' "
            "AND source_event_id LIKE 'islah:%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_tasks == 3
    assert n_created == 3


def test_up_is_idempotent(mig):
    m, db = mig["m"], mig["db"]
    m.up()
    conn = sqlite3.connect(str(db))
    first = conn.execute("SELECT count(*) FROM task_activity").fetchone()[0]
    conn.close()

    assert m.up() is True             # re-run
    conn = sqlite3.connect(str(db))
    try:
        second = conn.execute("SELECT count(*) FROM task_activity").fetchone()[0]
        n_tasks = conn.execute(
            "SELECT count(*) FROM tasks WHERE pipeline = 'bug'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert second == first            # no duplicate activity
    assert n_tasks == 3               # no duplicate tasks
