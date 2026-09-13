"""
aos#2298 — `datetime.UTC` is an attribute of the *datetime module*
(Python >= 3.11 only), never of the `datetime.datetime` class. Every
occurrence in this repo did `from datetime import datetime` and then read
`.UTC` off that class reference — which raises AttributeError on every
Python version, not only below AOS's stated 3.10 floor (verified directly:
`from datetime import datetime; datetime.UTC` fails identically even on this
box's 3.13 interpreter). Fixed everywhere by importing `timezone` alongside
`datetime` and calling `datetime.now(timezone.utc)`.

The buggy calls live inside `"$AOS_PY" -c "..."` heredocs embedded in bash
scripts (core/bin/internal/session-recorder, telemetry, check-fixed-issues),
so there is no importable Python module to load directly. Instead this test:

  1. Pins that the literal `datetime.UTC` pattern never reappears anywhere
     in the tracked tree (`git grep`).
  2. Extracts each affected inline python block verbatim from its bash host
     and executes it under a simulated pre-3.11 interpreter — monkeypatching
     the real `datetime` module to drop `UTC` (`raising=False`, exactly as
     the brief specifies) — proving the fixed code path never touches it.
"""

from __future__ import annotations

import datetime as datetime_module
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

AFFECTED_SCRIPTS = [
    REPO_ROOT / "core" / "bin" / "internal" / "session-recorder",
    REPO_ROOT / "core" / "bin" / "internal" / "telemetry",
    REPO_ROOT / "core" / "bin" / "internal" / "check-fixed-issues",
]


def _extract_python_blocks(script_text: str) -> list[str]:
    """Pull out the body of every `"$AOS_PY" -c "..."` heredoc, verbatim.

    Blocks open on a line ending in `-c "` and close on the next line whose
    first character is the closing `"` — true for every block in these
    three scripts (checked by hand: session-recorder has 8, telemetry 3,
    check-fixed-issues 3).
    """
    lines = script_text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        if re.search(r'-c\s+"\s*$', lines[i]):
            j = i + 1
            body = []
            while j < len(lines) and not lines[j].startswith('"'):
                body.append(lines[j])
                j += 1
            blocks.append("\n".join(body))
            i = j
        else:
            i += 1
    return blocks


def test_no_datetime_utc_pattern_in_tree():
    """git-grep, not a plain walk, so this only ever sees tracked files —
    and it excludes the two places the literal string is *expected* to
    appear as prose describing the bug (this file's own docstrings/
    assertions, and the CHANGELOG entry), not as a live code path."""
    result = subprocess.run(
        [
            "git", "grep", "-n", "datetime.UTC", "--",
            ".", ":!CHANGELOG.md", ":!tests/test_datetime_utc_removed.py",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 1, f"datetime.UTC reappeared:\n{result.stdout}"


def test_scripts_each_still_have_a_utcnow_call():
    """Guard against the fix silently deleting the timestamp instead of
    correcting it — every affected script must still call datetime.now()."""
    for script_path in AFFECTED_SCRIPTS:
        text = script_path.read_text()
        assert "datetime.now(timezone.utc)" in text, (
            f"{script_path.name} lost its datetime.now(timezone.utc) call"
        )
        assert ", timezone" in text or "import timezone" in text, (
            f"{script_path.name} calls timezone.utc without importing timezone"
        )


@pytest.fixture
def fake_pre311_datetime(monkeypatch):
    """Simulate a Python floor below 3.11: the datetime module has no UTC
    constant at all (added in 3.11)."""
    monkeypatch.delattr(datetime_module, "UTC", raising=False)
    return datetime_module


@pytest.mark.parametrize("script_path", AFFECTED_SCRIPTS, ids=lambda p: p.name)
def test_embedded_datetime_blocks_run_under_fake_pre311(
    script_path, fake_pre311_datetime, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"ts": "2026-01-01T00:00:00+00:00Z", "type": "flow_start"}\n'
    )

    # Every env var any of these blocks reads, pointed at tmp_path so nothing
    # touches the real ~/.aos or repo tree.
    monkeypatch.setenv("SESSION_FILE", str(session_file))
    monkeypatch.setenv("EVENT_TYPE", "note")
    monkeypatch.setenv("EVENT_DETAIL", "test")
    monkeypatch.setenv("EVENT_MESSAGE", "test message")
    monkeypatch.setenv("EVENT_CONTEXT", "test context")
    monkeypatch.setenv("EVENT_QUESTION", "test question")
    monkeypatch.setenv("EVENT_ANSWER", "test answer")
    monkeypatch.setenv("TEL_LOG", str(tmp_path / "telemetry.jsonl"))
    monkeypatch.setenv("TEL_USER_DIR", str(tmp_path))
    monkeypatch.setenv("TEL_MACHINE_HASH", "deadbeef")
    monkeypatch.setenv("TEL_FLOW", "onboard")
    monkeypatch.setenv("TEL_PHASE", "test")
    monkeypatch.setenv("TEL_ACTION", "complete")
    monkeypatch.setenv("TEL_DURATION", "1000")
    monkeypatch.setenv("LAST_CHECK", str(tmp_path / "last-check"))
    monkeypatch.setenv("FIXED_LOG", str(tmp_path / "fixed.jsonl"))

    text = script_path.read_text()
    blocks = _extract_python_blocks(text)
    datetime_blocks = [b for b in blocks if "datetime.now(" in b]
    assert datetime_blocks, f"expected a datetime.now() block in {script_path.name}"

    for block in datetime_blocks:
        assert "datetime.UTC" not in block, "fixed block must not read datetime.UTC"
        # Executed verbatim (not re-typed) so this proves the actual shipped
        # source, not a paraphrase of it.
        exec(compile(block, str(script_path), "exec"), {})
