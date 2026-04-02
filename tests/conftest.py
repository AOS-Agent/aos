"""
Shared fixtures for AOS work engine tests.

Every test that needs file I/O gets its own isolated tmp_path directory.
NEVER touches ~/.aos/ — all paths are redirected to pytest's tmp_path.
"""

import sys
from pathlib import Path

import pytest
import yaml

# Make the work package importable without installing it
WORK_DIR_SRC = Path(__file__).parent.parent / "core" / "engine" / "work"
if str(WORK_DIR_SRC) not in sys.path:
    sys.path.insert(0, str(WORK_DIR_SRC))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_empty_work_yaml(path: Path) -> Path:
    """Write a minimal empty work.yaml to *path* and return it."""
    data = {
        "version": "2.0",
        "tasks": [],
        "projects": [],
        "goals": [],
        "threads": [],
        "inbox": [],
    }
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return path


def make_work_yaml(path: Path, tasks=None, projects=None, goals=None,
                   threads=None, inbox=None) -> Path:
    """Write a work.yaml with the given lists to *path* and return it."""
    data = {
        "version": "2.0",
        "tasks": tasks or [],
        "projects": projects or [],
        "goals": goals or [],
        "threads": threads or [],
        "inbox": inbox or [],
    }
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False,
                               allow_unicode=True))
    return path


# ---------------------------------------------------------------------------
# Core fixture: isolated work directory wired into engine module-level vars
# ---------------------------------------------------------------------------

@pytest.fixture()
def work_env(tmp_path, monkeypatch):
    """Return a dict with work_dir / work_file pointing at tmp_path.

    Also patches engine.WORK_DIR, engine.WORK_FILE, engine.ACTIVITY_FILE,
    engine.LIVE_CONTEXT_FILE, and engine.LOCK_FILE so that all engine
    operations go to the temp directory rather than ~/.aos/work/.

    Usage:
        def test_something(work_env):
            import engine
            task = engine.add_task("Hello")
            assert task["title"] == "Hello"
    """
    # Re-import fresh each time so module-level state is clean
    import engine as eng

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    work_file = work_dir / "work.yaml"

    monkeypatch.setattr(eng, "WORK_DIR", work_dir)
    monkeypatch.setattr(eng, "WORK_FILE", work_file)
    monkeypatch.setattr(eng, "ACTIVITY_FILE", work_dir / "activity.yaml")
    monkeypatch.setattr(eng, "LIVE_CONTEXT_FILE", work_dir / ".live-context.json")
    monkeypatch.setattr(eng, "LOCK_FILE", work_dir / ".work.lock")

    return {
        "work_dir": work_dir,
        "work_file": work_file,
        "engine": eng,
    }


@pytest.fixture()
def populated_work_env(work_env):
    """work_env pre-seeded with a project and a few tasks."""
    eng = work_env["engine"]

    # Create the 'aos' project with short_id 'aos'
    eng.add_project("AOS Framework", short_id="aos", project_id="aos")

    # Add tasks under the project
    t1 = eng.add_task("Build session linking", project="aos", priority=2)
    t2 = eng.add_task("Write onboarding docs", project="aos", priority=3)
    t3 = eng.add_task("Unscoped task")  # no project

    work_env["t1"] = t1
    work_env["t2"] = t2
    work_env["t3"] = t3
    return work_env
