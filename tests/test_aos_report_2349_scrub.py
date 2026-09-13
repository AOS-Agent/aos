"""
Tests for aos#2349: aos-report's PII scrubber corrupts reports containing
code, tables, or dates.

Root cause (core/bin/cli/aos-report): `scrub()`'s phone regex —
`r'[\\+]?[\\d\\s\\-\\(\\)]{10,}'` — matched any 10+ run of digits, whitespace,
hyphens or parens. That ate indented code, markdown table separators, and
every ISO date, replacing them with `<phone>`.

Fix: `scrub()` now reuses the bridge's own `redact()`
(core/services/bridge/conversation_store.py, shipped 0.7.7) for phone/email
instead of a second, independently-drifting regex — it already excludes ISO
dates and requires a phone-shaped token boundary on both ends.

aos-report is a bare CLI script (no .py extension), loaded here by file path
— the same trick core/engine/notify/router.py uses to import
conversation_store.py from outside its own package (which is itself what
this fix reuses). The end-to-end test runs aos-report as a real child
process against a stub `gh` on PATH (HOME sandboxed to a tmp_path) — see
test_aos_report_2323_gh_repo.py's docstring for why that's the one path in
this suite allowed to reach a `gh`-shaped invocation.
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
    loader = SourceFileLoader("aos_report_under_test_2349", str(AOS_REPORT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def aos_report(monkeypatch, tmp_path):
    mod = _load_aos_report()
    monkeypatch.setattr(mod, "REPORT_LOG", tmp_path / "reports.jsonl")
    return mod


# ---------------------------------------------------------------------------
# Direct unit tests
# ---------------------------------------------------------------------------

class TestScrub:
    def test_phone_number_is_redacted(self, aos_report):
        out = aos_report.scrub("Call me at +1 416-555-0199 tomorrow.")
        assert "416-555-0199" not in out
        assert "[phone]" in out

    def test_code_block_survives_untouched(self, aos_report):
        code = "if x:\n            return False\n"
        out = aos_report.scrub(code)
        assert out == code

    def test_markdown_table_survives_untouched(self, aos_report):
        table = "| a | b |\n|------------|------------|\n| 1 | 2 |\n"
        out = aos_report.scrub(table)
        assert out == table

    def test_iso_datetime_survives_untouched(self, aos_report):
        text = "Filed at 2026-09-13T12:31:55 during the incident."
        out = aos_report.scrub(text)
        assert "2026-09-13T12:31:55" in out

    def test_git_sha_survives_untouched(self, aos_report):
        text = "Shipped at commit 7318093 on main."
        out = aos_report.scrub(text)
        assert "7318093" in out

    def test_combined_report_only_phone_is_redacted(self, aos_report):
        """The exact composite case from the aos#2349 brief: a code block, a
        table, an ISO timestamp, a sha, and a real-looking phone number — only
        the phone should change."""
        body = textwrap.dedent("""\
            ## Repro

            ```python
            if x:
                        return False
            ```

            | col A | col B |
            |------------|------------|
            | 1 | 2 |

            Seen since 2026-09-13T12:31:55, at commit 7318093.

            Contact: +1 416-555-0199
            """)
        out = aos_report.scrub(body)
        assert "return False" in out
        assert "|------------|------------|" in out
        assert "2026-09-13T12:31:55" in out
        assert "7318093" in out
        assert "416-555-0199" not in out
        assert "[phone]" in out

    def test_email_still_redacted(self, aos_report):
        out = aos_report.scrub("Reach operator@example.com for details.")
        assert "operator@example.com" not in out

    def test_uses_the_bridge_redactor_not_a_second_regex(self, aos_report):
        """scrub() must delegate to conversation_store.redact(), not keep an
        independent phone pattern (aos#2349)."""
        calls = []
        real = aos_report._load_redact()
        assert real is not None

        def spy(text):
            calls.append(text)
            return real(text)

        aos_report._redact = spy
        try:
            aos_report.scrub("call +1 416-555-0199 now")
        finally:
            aos_report._redact = real
        assert calls, "scrub() never invoked the bridge's redact()"


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
    def test_scrubber_end_to_end_only_phone_redacted(self, tmp_path, stub_gh):
        """The full aos#2349 acceptance case, driven through the real CLI."""
        bin_dir, gh_log = stub_gh
        home = tmp_path / "home"
        home.mkdir()
        body_text = textwrap.dedent("""\
            ```python
            if x:
                        return False
            ```

            | col A | col B |
            |------------|------------|
            | 1 | 2 |

            Seen at 2026-09-13T12:31:55, commit 7318093.

            Contact +1 416-555-0199 for a repro.
            """)
        payload = json.dumps({"title": "Scrub test", "body": body_text})

        result = _run_aos_report([], payload, home, bin_dir, gh_log)
        assert result.returncode == 0, result.stderr

        calls = _read_argv_log(gh_log)
        create_call = next(c for c in calls if c[:2] == ["issue", "create"])
        filed_body = create_call[create_call.index("--body") + 1]

        assert "return False" in filed_body
        assert "|------------|------------|" in filed_body
        assert "2026-09-13T12:31:55" in filed_body
        assert "7318093" in filed_body
        assert "416-555-0199" not in filed_body
        assert "[phone]" in filed_body
