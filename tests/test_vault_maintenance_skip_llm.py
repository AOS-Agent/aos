"""vault-maintenance's two Sonnet steps (topic refresh, synthesis drafts)
produced 0 tokens in/out and exactly one error every single night (cron
audit 2026-09-13, aos#240.5).

Root cause, confirmed by reading the code and ~/.aos/logs/crons/
vault-maintenance.log: both steps go through
core/engine/intelligence/compile/llm.py's `complete()`, which imports
`ExecutionRouter` from `core.engine.execution.router` /
`engine.execution.router` — a module that has never existed anywhere in
this tree (`git log` on `core/engine/execution/` returns nothing). Every
call fails immediately with `ModuleNotFoundError: No module named 'core'`
before any network request, which is why 0 tokens were ever spent. This
isn't a wrong-path typo to fix; `ExecutionRouter` is a real feature that was
never built. The orphan/stale detection (the two deterministic steps) work
correctly and are unaffected — the fix is to stop calling the two Sonnet
steps nightly (the runner's existing `skip_llm` knob, already built for
exactly this) rather than pretend a missing dependency works.

These tests pin: (1) `run_maintenance_pass(skip_llm=True)` records zero
errors and zero tokens for the LLM steps while orphans/stale still run, and
(2) the actual nightly cron script now passes `--skip-llm`.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "crons" / "vault-maintenance"

sys.path.insert(0, str(REPO))
from core.engine.intelligence.lint.runner import run_maintenance_pass  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_skip_llm_records_no_errors_and_no_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "vault" / "knowledge").mkdir(parents=True)

    report = _run(run_maintenance_pass(skip_llm=True))

    assert report.llm_skipped is True
    assert report.tokens_in == 0
    assert report.tokens_out == 0
    assert report.topics_refreshed == 0
    assert report.synthesis_drafted == 0
    # The whole point: no "unexpected ...: No module named 'core'" error,
    # because the broken code path is never entered at all.
    assert report.errors == [] or all(
        "execution" not in e.lower() and "module named" not in e.lower()
        for e in report.errors
    )


def test_llm_steps_are_not_even_attempted_when_skipped(tmp_path, monkeypatch):
    """Belt-and-suspenders: patch the two Sonnet-step entry points to raise
    if called at all, proving skip_llm short-circuits before them."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "vault" / "knowledge").mkdir(parents=True)

    import core.engine.intelligence.lint.synthesis_suggestions as ss
    import core.engine.intelligence.lint.topics_refresh as tr

    async def _boom(*a, **kw):
        raise AssertionError("should not be called when skip_llm=True")

    monkeypatch.setattr(tr, "refresh_topic_orientations", _boom)
    monkeypatch.setattr(ss, "draft_synthesis_suggestions", _boom)

    report = _run(run_maintenance_pass(skip_llm=True))
    assert report.error_count == 0 or all("should not be called" not in e for e in report.errors)


def test_nightly_cron_script_passes_skip_llm():
    """The actual cron invocation must disable the broken Sonnet steps."""
    text = SCRIPT.read_text()
    assert "--skip-llm" in text


def test_nightly_cron_script_invokes_the_runner_with_skip_llm_flag(tmp_path):
    """Run the real script against a stub interpreter, proving --skip-llm is
    an actual argv element passed to the runner module, not just a comment."""
    fake_home = tmp_path / "home"
    bin_dir = fake_home / "aos" / "core" / "bin" / "internal"
    lint_dir = fake_home / "aos" / "core" / "engine" / "intelligence" / "lint"
    bin_dir.mkdir(parents=True)
    lint_dir.mkdir(parents=True)

    call_log = tmp_path / "calls.log"
    fake_python = bin_dir / "aos-python"
    fake_python.write_text(
        "#!/bin/bash\necho \"$@\" >> \"$CALL_LOG\"\nexit 0\n"
    )
    fake_python.chmod(0o755)

    import os
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["CALL_LOG"] = str(call_log)
    # vault-maintenance hardcodes LINT_PYTHON to the crawler venv path and
    # checks -x on it directly rather than through PATH, so point that at
    # our stub for this test.
    (fake_home / ".aos" / "services" / "crawler" / ".venv" / "bin").mkdir(parents=True)
    stub = fake_home / ".aos" / "services" / "crawler" / ".venv" / "bin" / "python"
    stub.write_text("#!/bin/bash\necho \"$@\" >> \"$CALL_LOG\"\nexit 0\n")
    stub.chmod(0o755)

    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text() if call_log.exists() else ""
    assert "--skip-llm" in calls
    assert "-m engine.intelligence.lint.runner" in calls or "engine.intelligence.lint.runner" in calls
