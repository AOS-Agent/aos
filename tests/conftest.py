"""
Shared fixtures for AOS work engine tests.

Every test gets its own isolated SQLite work DB under pytest's tmp_path — the
same store the live CLI uses (core/engine/work/backend.py → WorkAdapter),
pointed away from ~/.aos/ via the AOS_WORK_DB override. No test ever touches
real operator data.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Make the work package importable without installing it. `backend` handles its
# own `core.qareen.*` path setup at import time.
WORK_DIR_SRC = Path(__file__).parent.parent / "core" / "engine" / "work"
if str(WORK_DIR_SRC) not in sys.path:
    sys.path.insert(0, str(WORK_DIR_SRC))

# The live migration-patched work-table schema, captured as a fixture. See
# tests/fixtures/work_schema.sql for how to regenerate it.
_WORK_SCHEMA = (Path(__file__).parent / "fixtures" / "work_schema.sql").read_text()


def _make_work_db(path: Path) -> Path:
    """Create an isolated work DB with the live schema at *path*."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_WORK_SCHEMA)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Suite-wide tripwire: no test may ever shell out to the `gh` CLI.
#
# The work engine syncs project="aos" tasks to GitHub Issues. Before it was
# guarded (AOS_GITHUB_SYNC opt-in + pytest detection), fixtures in this suite
# filed ~1,900 real issues on the public repo. The guard prevents that at the
# source; this tripwire makes any regression fail the suite loudly instead of
# silently spamming GitHub again.
# ---------------------------------------------------------------------------

_REAL_SUBPROCESS_RUN = subprocess.run


@pytest.fixture(autouse=True)
def no_gh_subprocess(monkeypatch):
    """Fail any test that tries to invoke the `gh` CLI."""

    def guarded_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        prog = ""
        if isinstance(cmd, (list, tuple)) and cmd:
            prog = str(cmd[0])
        elif isinstance(cmd, str):
            parts = cmd.split()
            prog = parts[0] if parts else ""
        if Path(prog).name == "gh":
            raise RuntimeError(
                f"Blocked `gh` invocation from the test suite: {cmd!r}. "
                "Tests must never reach GitHub — see AOS_GITHUB_SYNC guard."
            )
        return _REAL_SUBPROCESS_RUN(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)


# ---------------------------------------------------------------------------
# Core fixture: an isolated work engine bound to a throwaway SQLite DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def work_env(tmp_path, monkeypatch):
    """Return a dict wired to an isolated backend instance.

    Keys:
        engine    the backend module (the live CLI's work engine)
        db_path   the throwaway SQLite DB backing it
        work_dir  a scratch dir for the activity log

    Usage:
        def test_something(work_env):
            eng = work_env["engine"]
            task = eng.add_task("Hello")
            assert task["title"] == "Hello"
    """
    import backend as eng

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    db_path = _make_work_db(tmp_path / "work.db")

    # Point the engine at the isolated DB. AOS_WORK_DB is what the resolution
    # logic reads; DB_PATH is the already-resolved module global.
    monkeypatch.setenv("AOS_WORK_DB", str(db_path))
    monkeypatch.setattr(eng, "DB_PATH", db_path)
    monkeypatch.setattr(eng, "WORK_DIR", work_dir)
    monkeypatch.setattr(eng, "ACTIVITY_FILE", work_dir / "activity.yaml")

    # Rebind the cached singletons so they open the isolated DB, not a real one.
    monkeypatch.setattr(eng, "_adapter", None)
    monkeypatch.setattr(eng, "_resolver", None)
    monkeypatch.setattr(eng, "_project_ctx", None)

    return {
        "work_dir": work_dir,
        "db_path": db_path,
        "engine": eng,
    }


@pytest.fixture()
def populated_work_env(work_env):
    """work_env pre-seeded with a project and a few tasks."""
    eng = work_env["engine"]

    eng.add_project("AOS Framework", short_id="aos", project_id="aos")

    work_env["t1"] = eng.add_task("Build session linking", project="aos", priority=2)
    work_env["t2"] = eng.add_task("Write onboarding docs", project="aos", priority=3)
    work_env["t3"] = eng.add_task("Unscoped task")  # no project
    return work_env
