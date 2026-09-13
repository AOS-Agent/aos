"""
Tests for aos#2338: aos-report discards the report body when queueing, so a
queued report can never actually be filed later.

Root cause (core/bin/cli/aos-report): `log_report()` (pre-fix, line 268)
took `title`/`labels`/`issue_url`/`action` but no `body` — when GitHub
filing failed and the report was queued, the diagnosis itself (the entire
value of a bug report) was never written anywhere. `--queued` would still
list the title, so the queue looked healthy while being an empty shell: no
code path could ever file any of it.

Fix: `log_report()` now persists the full body (and, for a duplicate-of
comment, the target issue number) plus a unique `id`; a new `drain_queue()`
(wired to `aos-report --drain`) files every still-pending queued entry with
exactly that payload, and marks it `drained_from` so it stops showing up as
still queued.

aos-report is a bare CLI script (no .py extension), loaded here by file path
— the same trick core/engine/notify/router.py uses to import
conversation_store.py from outside its own package. End-to-end tests run it
as a real child process against a stub `gh` on PATH (HOME sandboxed to a
tmp_path) — see test_aos_report_2323_gh_repo.py's docstring for why that's
the one path in this suite allowed to reach a `gh`-shaped invocation.
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
    loader = SourceFileLoader("aos_report_under_test_2338", str(AOS_REPORT))
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
# Direct unit tests
# ---------------------------------------------------------------------------

class TestQueueAndDrain:
    def test_log_report_persists_body(self, aos_report):
        entry_id = aos_report.log_report(
            "Title", ["bug"], None, "queued", body="the full diagnosis body"
        )
        lines = aos_report.REPORT_LOG.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["id"] == entry_id
        assert entry["body"] == "the full diagnosis body"
        assert entry["action"] == "queued"

    def test_pending_queue_lists_unresolved_entries(self, aos_report):
        aos_report.log_report("A", ["bug"], None, "queued", body="body A")
        aos_report.log_report("B", ["bug"], None, "queued", body="body B")
        pending = aos_report._pending_queue_entries()
        assert {e["title"] for e in pending} == {"A", "B"}

    def test_drain_files_exactly_what_was_queued(self, aos_report, monkeypatch):
        original_body = "## Root cause\n\nfull diagnosis text, not just a title"
        aos_report.log_report(
            "Bridge crashes on empty schedule", ["bug", "has-fix"], None,
            "queued", body=original_body,
        )

        captured = {}

        def fake_file_issue(title, body, labels):
            captured["title"] = title
            captured["body"] = body
            captured["labels"] = labels
            return {"number": 1, "url": "https://github.com/AOS-Agent/aos/issues/1"}, None

        monkeypatch.setattr(aos_report, "file_issue", fake_file_issue)

        summary = aos_report.drain_queue()

        assert captured["title"] == "Bridge crashes on empty schedule"
        assert captured["body"] == original_body
        assert captured["labels"] == ["bug", "has-fix"]
        assert summary["filed"] == [
            {"title": "Bridge crashes on empty schedule",
             "issue_url": "https://github.com/AOS-Agent/aos/issues/1"}
        ]
        assert summary["still_queued"] == []

        # Drained entry no longer shows up as pending.
        assert aos_report._pending_queue_entries() == []

    def test_drain_leaves_still_failing_entries_queued(self, aos_report, monkeypatch):
        aos_report.log_report("Still broken", ["bug"], None, "queued", body="x")
        monkeypatch.setattr(
            aos_report, "file_issue",
            lambda title, body, labels: (None, "not authenticated"),
        )
        summary = aos_report.drain_queue()
        assert summary["still_queued"] == ["Still broken"]
        assert summary["errors"] == [{"title": "Still broken", "error": "not authenticated"}]
        pending = aos_report._pending_queue_entries()
        assert [e["title"] for e in pending] == ["Still broken"]

    def test_drain_of_duplicate_of_entry_uses_comment_on_issue(self, aos_report, monkeypatch):
        aos_report.log_report(
            "Dup report", ["bug"], None, "queued", body="extra context",
            duplicate_of=2323,
        )
        captured = {}

        def fake_comment(number, body):
            captured["number"] = number
            captured["body"] = body
            return True, None

        monkeypatch.setattr(aos_report, "comment_on_issue", fake_comment)
        summary = aos_report.drain_queue()

        assert captured == {"number": 2323, "body": "extra context"}
        assert summary["filed"][0]["issue_url"] == "https://github.com/AOS-Agent/aos/issues/2323"
        assert aos_report._pending_queue_entries() == []


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
    def test_queue_then_drain_round_trip_preserves_body(self, tmp_path, stub_gh):
        """What gets queued while gh is failing must be exactly what gets
        filed once --drain succeeds — title, labels, and the full body, not
        just metadata."""
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        body_text = (
            "## Root cause\n\ndetailed diagnosis with a code block:\n\n"
            "```\nif x:\n    return None\n```\n"
        )
        payload = json.dumps({
            "title": "Detailed queued bug",
            "body": body_text,
            "labels": ["bug", "has-fix"],
        })

        queued = _run_aos_report([], payload, home, bin_dir, gh_log, stub_gh_mode="fail")
        assert queued.returncode != 0
        assert json.loads(queued.stdout)["action"] == "queued"

        report_log = home / ".aos" / "logs" / "reports.jsonl"
        assert report_log.exists()
        queued_entries = [json.loads(l) for l in report_log.read_text().splitlines()]
        assert len(queued_entries) == 1
        assert queued_entries[0]["action"] == "queued"
        assert "Detailed queued bug" in queued_entries[0]["title"]
        assert "detailed diagnosis" in queued_entries[0]["body"]

        drained = _run_aos_report(["--drain"], "", home, bin_dir, gh_log, stub_gh_mode="success")
        assert drained.returncode == 0, drained.stderr
        summary = json.loads(drained.stdout)
        assert summary["attempted"] == 1
        assert summary["still_queued"] == []
        assert len(summary["filed"]) == 1
        assert summary["filed"][0]["title"] == queued_entries[0]["title"]

        # The gh call the drain made must carry the identical body that was
        # queued — not a placeholder, not just the title.
        calls = _read_argv_log(gh_log)
        create_calls = [c for c in calls if c[:2] == ["issue", "create"]]
        assert create_calls, "drain never re-attempted `gh issue create`"
        last_create = create_calls[-1]
        assert "--body" in last_create
        drained_body = last_create[last_create.index("--body") + 1]
        assert drained_body == queued_entries[0]["body"]

        # And it no longer shows up as still queued.
        queued_now = _run_aos_report(["--queued"], "", home, bin_dir, gh_log)
        assert json.loads(queued_now.stdout)["count"] == 0

    def test_drain_with_nothing_queued_is_a_clean_noop(self, tmp_path, stub_gh):
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        result = _run_aos_report(["--drain"], "", home, bin_dir, gh_log)
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary == {"attempted": 0, "filed": [], "still_queued": [], "errors": []}
