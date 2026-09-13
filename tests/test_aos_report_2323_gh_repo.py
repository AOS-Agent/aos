"""
Tests for aos#2323: aos-report's `gh` calls silently failing on every
release install.

Root cause (core/bin/cli/aos-report): `file_issue()`/`comment_on_issue()`/
`search_existing_issues()` ran `gh` with `cwd=str(AOS_DIR)` and no `--repo` —
on a release install `~/aos` is a symlink into an unpacked tarball with no
`.git`, so `gh` could never infer a target repo, and every report silently
fell back to the local queue with no visible failure (exit 0).

Fix: all three calls now pass `--repo AOS-Agent/aos` (the one `GH_REPO`
constant) and never depend on `cwd`; `file_issue`/`comment_on_issue` return
`(result, error)` instead of swallowing the failure, and the queue-fallback
path in `main()` exits non-zero with a plain-English notice.

aos-report is a bare CLI script (no .py extension), loaded here by file path
— the same trick core/engine/notify/router.py uses to import
conversation_store.py from outside its own package.

End-to-end tests run aos-report as a real child process against a recording
stub `gh` placed first on PATH, with HOME sandboxed to a tmp_path.
tests/conftest.py's suite-wide `no_gh_subprocess` autouse fixture only blocks
a `subprocess.run(["gh", ...])` reached from *inside this pytest process* (it
exists because work-engine fixtures used to leak real `gh` calls onto the
public repo) — a spawned child process has its own, unpatched subprocess
module, so this is the one path in the suite that can exercise a real
`gh`-shaped invocation, as long as what's on PATH is the stub, never the real
CLI.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import textwrap
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AOS_REPORT = REPO_ROOT / "core" / "bin" / "cli" / "aos-report"


def _load_aos_report():
    """aos-report has no `.py` suffix, so spec_from_file_location can't infer
    a loader on its own — same fix test_weekly_digest.py and others use for
    the other extensionless scripts under core/bin/."""
    loader = SourceFileLoader("aos_report_under_test_2323", str(AOS_REPORT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def aos_report(monkeypatch, tmp_path):
    """The aos-report module, with its report log pointed at a scratch file."""
    mod = _load_aos_report()
    monkeypatch.setattr(mod, "REPORT_LOG", tmp_path / "reports.jsonl")
    return mod


# ---------------------------------------------------------------------------
# Direct unit tests: every gh call must carry --repo, never rely on cwd;
# failures must return an error, not silently None.
# ---------------------------------------------------------------------------

class TestGhRepoAndFailureSurfacing:
    def _stub_run(self, monkeypatch, aos_report, returncode=0, stdout="", stderr=""):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, "kwargs": kwargs})
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        monkeypatch.setattr(aos_report.subprocess, "run", fake_run)
        return calls

    def test_file_issue_passes_repo_flag_and_no_cwd(self, aos_report, monkeypatch):
        calls = self._stub_run(
            monkeypatch, aos_report, returncode=0,
            stdout="https://github.com/AOS-Agent/aos/issues/42\n",
        )
        result, err = aos_report.file_issue("Title", "Body", ["bug"])
        assert err is None
        assert result == {"number": 42, "url": "https://github.com/AOS-Agent/aos/issues/42"}
        assert len(calls) == 1
        cmd = calls[0]["cmd"]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "AOS-Agent/aos"
        assert "cwd" not in calls[0]["kwargs"]

    def test_comment_on_issue_passes_repo_flag_and_no_cwd(self, aos_report, monkeypatch):
        calls = self._stub_run(monkeypatch, aos_report, returncode=0, stdout="ok\n")
        ok, err = aos_report.comment_on_issue(7, "hello")
        assert ok is True
        assert err is None
        cmd = calls[0]["cmd"]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "AOS-Agent/aos"
        assert "cwd" not in calls[0]["kwargs"]

    def test_search_existing_issues_passes_repo_flag_and_no_cwd(self, aos_report, monkeypatch):
        calls = self._stub_run(monkeypatch, aos_report, returncode=0, stdout="[]")
        aos_report.search_existing_issues("some bug title")
        cmd = calls[0]["cmd"]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "AOS-Agent/aos"
        assert "cwd" not in calls[0]["kwargs"]

    def test_file_issue_failure_returns_error_not_none_silently(self, aos_report, monkeypatch):
        self._stub_run(monkeypatch, aos_report, returncode=1, stderr="not authenticated")
        result, err = aos_report.file_issue("Title", "Body", ["bug"])
        assert result is None
        assert err == "not authenticated"

    def test_comment_on_issue_failure_returns_error(self, aos_report, monkeypatch):
        self._stub_run(monkeypatch, aos_report, returncode=1, stderr="issue not found")
        ok, err = aos_report.comment_on_issue(999, "hello")
        assert ok is False
        assert err == "issue not found"

    def test_file_issue_gh_missing_returns_error_not_raise(self, aos_report, monkeypatch):
        def boom(cmd, **kwargs):
            raise FileNotFoundError("gh: command not found")

        monkeypatch.setattr(aos_report.subprocess, "run", boom)
        result, err = aos_report.file_issue("Title", "Body", ["bug"])
        assert result is None
        assert err is not None and "gh" in err


# ---------------------------------------------------------------------------
# End-to-end: real subprocess, stub `gh` on PATH, sandboxed HOME
# ---------------------------------------------------------------------------

_STUB_GH_TEMPLATE = textwrap.dedent("""\
    {shebang}
    # Stub `gh` for aos-report end-to-end tests — records argv, never touches
    # the network. Mode from $STUB_GH_MODE: "success" (default) or "fail".
    import json
    import os
    import sys

    log_path = os.environ["STUB_GH_LOG"]
    mode = os.environ.get("STUB_GH_MODE", "success")

    with open(log_path, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")

    if mode == "fail":
        sys.stderr.write("stub gh: simulated failure (not authenticated)\\n")
        sys.exit(1)

    argv = sys.argv[1:]
    if argv[:2] == ["issue", "create"]:
        print("https://github.com/AOS-Agent/aos/issues/99999")
    elif argv[:2] == ["issue", "comment"]:
        print("https://github.com/AOS-Agent/aos/issues/99999#issuecomment-1")
    elif argv[:2] == ["issue", "list"]:
        print("[]")
    sys.exit(0)
    """)


@pytest.fixture()
def stub_gh(tmp_path):
    """A recording stub `gh` in its own bin dir, plus the log path it writes to."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(_STUB_GH_TEMPLATE.format(shebang="#!/usr/bin/env python3"))
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log_path = tmp_path / "gh-calls.jsonl"
    return bin_dir, log_path


def _read_argv_log(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _run_aos_report(args, stdin_data, home, stub_bin_dir, gh_log, stub_gh_mode="success"):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["STUB_GH_LOG"] = str(gh_log)
    env["STUB_GH_MODE"] = stub_gh_mode
    env["PATH"] = f"{stub_bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(AOS_REPORT), *args],
        input=stdin_data, capture_output=True, text=True, timeout=30, env=env,
    )


class TestEndToEnd:
    def test_create_success_uses_repo_flag_and_no_cwd_dependency(self, tmp_path, stub_gh):
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        # No ~/aos at all in this HOME — proves nothing depends on cwd/AOS_DIR
        # being a git repo (aos#2323's actual bug: ~/aos is a release symlink
        # with no .git on real installs).
        payload = json.dumps({"title": "Something broke", "body": "Steps to repro."})

        result = _run_aos_report([], payload, home, bin_dir, gh_log)

        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["action"] == "created"

        calls = _read_argv_log(gh_log)
        assert calls, "stub gh was never invoked"
        create_call = next(c for c in calls if c[:2] == ["issue", "create"])
        assert "--repo" in create_call
        assert create_call[create_call.index("--repo") + 1] == "AOS-Agent/aos"

    def test_duplicate_of_uses_repo_flag(self, tmp_path, stub_gh):
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        payload = json.dumps({
            "title": "Dup of something",
            "body": "Same bug again.",
            "duplicate_of": 2323,
        })

        result = _run_aos_report([], payload, home, bin_dir, gh_log)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["action"] == "commented on #2323"

        calls = _read_argv_log(gh_log)
        comment_call = next(c for c in calls if c[:2] == ["issue", "comment"])
        assert "--repo" in comment_call
        assert comment_call[comment_call.index("--repo") + 1] == "AOS-Agent/aos"

    def test_gh_failure_propagates_nonzero_exit_and_humanized_notice(self, tmp_path, stub_gh):
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        payload = json.dumps({"title": "Something broke", "body": "Steps to repro."})

        result = _run_aos_report([], payload, home, bin_dir, gh_log, stub_gh_mode="fail")

        # aos#2323: a failed gh call must surface — non-zero exit, never a
        # silent success — plus a plain-English notice, not just JSON.
        assert result.returncode != 0
        assert "GitHub filing failed" in result.stderr
        assert "not authenticated" in result.stderr
        out = json.loads(result.stdout)
        assert out["action"] == "queued"
        assert "error" in out
