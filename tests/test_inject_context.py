"""
Test suite for AOS Context Injection Hook (core/work/inject_context.py).

Tests exercise the module's output contract:
  - Always produces valid JSON
  - Contains the "additionalContext" key
  - Handles empty or missing work.yaml without crashing
  - Active tasks appear in context
  - High-priority tasks (P1/P2) are surfaced

Strategy: we call inject_context.main() via subprocess so that we exercise
the full hook pipeline (stdin JSON in, stdout JSON out) without polluting
this process's module state. We redirect WORK_DIR by setting environment
variables — but since engine reads from module-level constants we instead
monkeypatch engine state before calling main() directly via import.

For subprocess tests we write a tiny wrapper that patches the work dir
from an environment variable before importing inject_context.
"""

import json
import os
import sys
import subprocess
import tempfile
import yaml
import pytest
from pathlib import Path

WORK_PKG = Path(__file__).parent.parent / "core" / "work"
sys.path.insert(0, str(WORK_PKG))


# ---------------------------------------------------------------------------
# Helper: run inject_context.main() in a subprocess with a custom work dir
# ---------------------------------------------------------------------------

_RUNNER_TEMPLATE = """
import sys, os, json
sys.path.insert(0, {work_pkg!r})

# Patch engine paths before any import resolves the module-level constants
import engine
from pathlib import Path
_work_dir = Path({work_dir!r})
engine.WORK_DIR = _work_dir
engine.WORK_FILE = _work_dir / "work.yaml"
engine.ACTIVITY_FILE = _work_dir / "activity.yaml"
engine.LIVE_CONTEXT_FILE = _work_dir / ".live-context.json"
engine.LOCK_FILE = _work_dir / ".work.lock"

# Now import inject_context — it re-imports engine but we already patched it
import inject_context

# Feed synthetic hook input
hook_input = {hook_input!r}
import io
sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(hook_input).encode()))

inject_context.main()
"""


def run_inject_context(work_dir: Path, hook_input: dict = None) -> dict:
    """Run inject_context.main() in a subprocess, return parsed JSON output."""
    if hook_input is None:
        hook_input = {"session_id": "test-session-001", "cwd": str(work_dir)}

    script = _RUNNER_TEMPLATE.format(
        work_pkg=str(WORK_PKG),
        work_dir=str(work_dir),
        hook_input=hook_input,
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, \
        f"inject_context hook exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"

    output_line = result.stdout.strip()
    assert output_line, f"inject_context produced no output.\nstderr: {result.stderr}"

    return json.loads(output_line)


def _write_work(work_dir: Path, tasks=None, projects=None):
    work_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "2.0",
        "tasks": tasks or [],
        "projects": projects or [],
        "goals": [],
        "threads": [],
        "inbox": [],
    }
    (work_dir / "work.yaml").write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True)
    )


# ===========================================================================
# Output Format — 3 tests
# ===========================================================================

class TestOutputFormat:

    def test_output_is_valid_json_with_additionalcontext_key(self, tmp_path):
        """inject_context always outputs valid JSON with an 'additionalContext' key."""
        work_dir = tmp_path / "work"
        _write_work(work_dir)

        output = run_inject_context(work_dir)

        assert isinstance(output, dict), \
            "Output must be a JSON object (dict)"
        assert "additionalContext" in output, \
            f"Output must contain 'additionalContext' key, got keys: {list(output.keys())}"
        assert isinstance(output["additionalContext"], str), \
            "additionalContext must be a string"

    def test_works_with_no_work_yaml(self, tmp_path):
        """inject_context exits cleanly even when work.yaml does not exist."""
        work_dir = tmp_path / "empty_work"
        work_dir.mkdir()
        # No work.yaml written

        output = run_inject_context(work_dir)

        # Must be valid JSON — key may or may not be present depending on branch
        assert isinstance(output, dict), \
            "Output must be a JSON object even with no work.yaml"

    def test_works_with_tasks_but_none_active(self, tmp_path):
        """inject_context handles work.yaml with only todo tasks (none active)."""
        work_dir = tmp_path / "work"
        _write_work(work_dir, tasks=[
            {"id": "t#1", "title": "Backlog task", "status": "todo", "priority": 3,
             "created": "2026-01-01", "source": "manual"},
        ])

        output = run_inject_context(work_dir)

        assert "additionalContext" in output, \
            "Must produce additionalContext when tasks exist but none are active"
        # Should not crash trying to list active tasks when there are none
        context = output["additionalContext"]
        assert isinstance(context, str) and len(context) > 0, \
            "Context string must be non-empty"


# ===========================================================================
# Content — 2 tests
# ===========================================================================

class TestContextContent:

    def test_active_tasks_appear_in_context(self, tmp_path):
        """Tasks with status='active' are surfaced in the injected context."""
        work_dir = tmp_path / "work"
        _write_work(work_dir, tasks=[
            {"id": "aos#7", "title": "Active feature work", "status": "active",
             "priority": 2, "project": "aos", "created": "2026-01-01",
             "source": "manual"},
            {"id": "aos#8", "title": "Idle backlog item", "status": "todo",
             "priority": 4, "project": "aos", "created": "2026-01-01",
             "source": "manual"},
        ])

        output = run_inject_context(work_dir)
        context = output.get("additionalContext", "")

        assert "Active feature work" in context, \
            "Active task title must appear in injected context"

    def test_high_priority_tasks_are_highlighted(self, tmp_path):
        """Priority 1 and 2 todo tasks appear under a 'High priority' section."""
        work_dir = tmp_path / "work"
        _write_work(work_dir, tasks=[
            {"id": "t#1", "title": "Urgent P1 task", "status": "todo",
             "priority": 1, "created": "2026-01-01", "source": "manual"},
            {"id": "t#2", "title": "Important P2 task", "status": "todo",
             "priority": 2, "created": "2026-01-01", "source": "manual"},
            {"id": "t#3", "title": "Normal priority task", "status": "todo",
             "priority": 3, "created": "2026-01-01", "source": "manual"},
        ])

        output = run_inject_context(work_dir)
        context = output.get("additionalContext", "")

        assert "Urgent P1 task" in context, \
            "P1 task must appear in context"
        assert "Important P2 task" in context, \
            "P2 task must appear in context"
        # P3 task should not appear in the high-priority section
        # (it may appear elsewhere but the section should only highlight P1/P2)
        assert "High priority" in context, \
            "Context must contain a 'High priority' heading section"
