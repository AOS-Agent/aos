"""The work surfaces retired in v0.7.7 stay retired.

Five things left the work engine in this release, each for the same reason:
the code shipped, ran, and was never used.

  * ``engine.py`` (2,148 lines) — the predecessor to ``backend.py``, still
    sitting beside its replacement with a second, diverged copy of the
    thread-creation logic. Nothing in the CLI imported it; the only live
    callers were three dynamic ``spec_from_file_location`` loads in the
    Telegram bridge, which now load ``backend.py`` instead.
  * ``runner.py`` + ``proc_group.py`` + the ``work-runner`` service — a task
    delegation daemon with a resident LaunchAgent and **zero rows in
    ``task_runs``, ever**, on every machine audited. Migration 111 switched it
    off fleet-wide; migration 124 removes the deployed plist and its instance
    config now that the code is gone.
  * ``metrics.py`` — computed flow metrics from ``~/.aos/work/work.yaml``, a
    file nothing has written since 2026-04-02. The one command an operator
    would run to ask "how is work going" answered from a five-month-old
    fossil, with no staleness warning.
  * the ``delegate`` / ``hold`` / ``runner`` / ``metrics`` CLI commands — 0, 3,
    2 and 0 invocations respectively across the tool's whole life.

This test is the ratchet. A module that was dead once is attractive to
resurrect by accident (a stray import, a revert, a merge that brings a file
back), and the failure mode is silent: the re-added file just sits there
again. Asserting absence is cheap; noticing 2,000 lines of dead code a second
time took an audit.

``backend.delegate_task`` / ``backend.hold_task`` deliberately survive — the
delegation *state transition* is still exercised by the Kanban narration tests
(test_kanban_phase1/2) and is part of the adapter facade. What went is the
operator-facing surface, not the state model.
"""

from __future__ import annotations

from pathlib import Path

WORK_PKG = Path(__file__).resolve().parents[3] / "core" / "engine" / "work"
REPO = Path(__file__).resolve().parents[3]

RETIRED_MODULES = (
    "engine.py",
    "runner.py",
    "proc_group.py",
    "metrics.py",
)

RETIRED_COMMANDS = ("delegate", "hold", "runner", "metrics")

# The core loop the audit found real traffic for, plus the five low-volume
# commands the operator explicitly kept. Guards against over-deleting.
KEPT_COMMANDS = (
    "add", "done", "start", "list", "show", "search", "today", "next",
    "projects", "inbox", "link", "subtask", "handoff", "dispatch", "thread",
    "who", "move", "promote", "goals", "threads",
)


def test_retired_modules_are_gone():
    present = [m for m in RETIRED_MODULES if (WORK_PKG / m).exists()]
    assert present == [], (
        f"{present} came back into core/engine/work/ — these were removed in "
        "v0.7.7 as unreachable or fossil-reading code; see this module's "
        "docstring before re-adding one"
    )


def test_work_runner_service_is_gone():
    leftovers = [
        p
        for p in (
            REPO / "core" / "services" / "work_runner",
            REPO / "config" / "defaults" / "work-runner.yaml",
        )
        if p.exists()
    ]
    assert leftovers == [], (
        f"work-runner service artefacts are back: {leftovers}. The plist "
        "template is the dangerous one — an installable template means a "
        "machine can deploy a LaunchAgent for code that no longer exists."
    )


def test_modules_yaml_no_longer_declares_work_runner():
    import yaml

    manifest = yaml.safe_load((REPO / "config" / "modules.yaml").read_text()) or {}
    ids = {m.get("id") for m in (manifest.get("modules") or [])}
    assert "work-runner" not in ids, (
        "config/modules.yaml still offers work-runner as a module — the "
        "registry would let an operator opt in to a service with no code"
    )


def test_cli_no_longer_dispatches_the_retired_commands():
    import cli

    for name in RETIRED_COMMANDS:
        assert name not in cli.COMMANDS, f"`work {name}` is back in the dispatch table"
        assert name not in cli.USAGE, f"`work {name}` is back in the usage table"
        assert not hasattr(cli, f"cmd_{name}"), f"cli.cmd_{name} is back"


def test_cli_still_dispatches_everything_that_was_kept():
    import cli

    missing = [c for c in KEPT_COMMANDS if c not in cli.COMMANDS]
    assert missing == [], f"removal went too far — lost commands: {missing}"


def test_nothing_in_the_work_engine_imports_the_old_engine():
    """The grep the audit should have run. `import engine` resolves flat off
    sys.path inside this package, so a single stray line silently revives a
    module that no longer exists and fails at runtime, not at import."""
    offenders = []
    for py in sorted(WORK_PKG.rglob("*.py")):
        text = py.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import engine", "from engine import")):
                offenders.append(f"{py.relative_to(REPO)}:{lineno}")
    assert offenders == [], f"references to the removed engine.py: {offenders}"


def test_bridge_loads_the_live_backend_not_the_removed_engine():
    """daily_briefing.py and evening_checkin.py load the work engine by path
    (spec_from_file_location), so a deleted file degrades to a silent
    "work engine unavailable" and an empty briefing rather than an ImportError
    anyone notices."""
    bridge = REPO / "core" / "services" / "bridge"
    offenders = []
    for name in ("daily_briefing.py", "evening_checkin.py"):
        text = (bridge / name).read_text()
        if '"work" / "engine.py"' in text or '"engine.py"' in text:
            offenders.append(name)
    assert offenders == [], (
        f"{offenders} still load core/engine/work/engine.py by path; they must "
        "load backend.py, which carries the same get_all_tasks() contract"
    )
