"""Regression test for the compute.py sys.path bootstrap.

The comms-patterns cron runs
``python3 <repo>/core/engine/comms/patterns/compute.py`` from the scheduler
LaunchAgent: a foreign working directory, a minimal environment with no
PYTHONPATH, and ``~/aos`` symlinked to a versioned release dir. compute.py must
bootstrap its own ``import db`` (the people package, a sibling under
core/engine/) from that context. A previous bootstrap walked the resolved path
for a parent literally named ``engine``; this test reproduces the scheduler
invocation and fails if the bootstrap ever regresses to "No module named 'db'"
or an "attempted relative import" error.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tests/engine/comms/ -> repo root is parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]
COMPUTE = REPO_ROOT / "core" / "engine" / "comms" / "patterns" / "compute.py"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"


def test_compute_bootstraps_db_from_scheduler_context(tmp_path):
    """compute.py resolves the people package from a scheduler-equivalent run."""
    assert COMPUTE.exists(), f"compute.py missing at {COMPUTE}"

    # Mimic the LaunchAgent: foreign cwd, no PYTHONPATH, minimal env. We invoke
    # the same interpreter running the tests (matching the scheduler, which
    # pins an absolute python), by absolute path, so PATH is irrelevant.
    result = subprocess.run(
        [sys.executable, str(COMPUTE), "--dry-run"],
        cwd=str(tmp_path),
        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = result.stdout + result.stderr

    # The bootstrap must resolve `import db` regardless of cwd / env / symlink.
    assert "No module named 'db'" not in combined, combined
    assert "ModuleNotFoundError" not in combined, combined
    assert "attempted relative import" not in combined, combined

    # When the people DB is present (the real scheduler condition) the dry run
    # must complete cleanly end-to-end.
    if PEOPLE_DB.exists():
        assert result.returncode == 0, combined
