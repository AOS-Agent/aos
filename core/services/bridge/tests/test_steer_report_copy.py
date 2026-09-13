"""A failed STEER job must not text the operator a stack trace (aos#235).

The four strings the 2026-09-13 review pulled verbatim out of
`_dispatch_steer_and_report`:

    ❌ Failed to dispatch STEER job: {stderr[:200]}
    🔄 Working on it...\\n<code>job: {job_id}</code>
    🔄 Working...\\n<code>{latest[:100]}</code>
    ⏰ Job timed out after {max_wait}s.\\n<code>job: {job_id}</code>

This drives the real function with a stubbed `subprocess.run` and asserts what
actually reaches the wire. Runs in the bridge venv:

    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import telegram_channel as tc  # noqa: E402

RAW_STDERR = '''Traceback (most recent call last):
  File "/srv/aos/core/steer/dispatch.py", line 214, in run
    job = build(spec)
RuntimeError: tmux session refused
'''

_PATH = re.compile(r"~?/[\w./\-]+/[\w./\-]+")
_FILE = re.compile(r"\b[\w.\-]+\.(?:py|yaml|yml|json|toml|log|plist|sql|sh)\b")
_TRACEBACK = re.compile(r"Traceback|File \"|, line \d+|[A-Za-z]+Error:")
_JOBID = re.compile(r"\bjob[:_ -]?[0-9a-f]{6,}\b|\b[0-9a-f]{8,}\b", re.I)

JOB_ID = "a1b2c3d4e5f6"


def _assert_phone_safe(text: str):
    assert not _PATH.search(text), f"path leaked: {text!r}"
    assert not _FILE.search(text), f"filename leaked: {text!r}"
    assert not _TRACEBACK.search(text), f"traceback leaked: {text!r}"
    assert not _JOBID.search(text), f"job id leaked: {text!r}"
    assert "<code>" not in text, f"raw <code> block leaked: {text!r}"


def _items(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip()])


class _Msg:
    def __init__(self):
        self.sent: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self


class _Chat:
    def __init__(self):
        self.sent: list[str] = []
        self.last = _Msg()

    async def send_message(self, text, **kwargs):
        self.sent.append(text)
        return self.last


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))


def _completed(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def test_dispatch_failure_sends_no_stderr(monkeypatch):
    """The done-when: a forced STEER failure with raw stderr on the wire."""
    monkeypatch.setattr(tc.subprocess, "run",
                        lambda *a, **kw: _completed(stderr=RAW_STDERR, rc=1))

    chat, msg = _Chat(), _Msg()
    asyncio.run(tc._dispatch_steer_and_report(chat, msg, "open the calendar"))

    assert msg.sent, "nothing was sent to the operator"
    text = msg.sent[0]
    _assert_phone_safe(text)
    assert _items(text) <= 4, text
    assert "tmux" not in text


def test_acknowledgement_carries_no_job_id(monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, *a, **kw):
        calls["n"] += 1
        if "run" in cmd:
            return _completed(stdout=json.dumps({"job_id": JOB_ID}))
        # Poll: report completion immediately so the test does not sleep long.
        return _completed(stdout=json.dumps({"status": "completed",
                                            "summary": "Opened the calendar."}))

    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    monkeypatch.setattr(tc.asyncio, "sleep", _instant_sleep)

    chat, msg = _Chat(), _Msg()
    asyncio.run(tc._dispatch_steer_and_report(chat, msg, "open the calendar"))

    assert chat.sent, "no acknowledgement sent"
    _assert_phone_safe(chat.sent[0])
    assert JOB_ID not in chat.sent[0]
    assert chat.sent[0] == "🔄 On it."


def test_job_failure_report_carries_no_raw_error(monkeypatch):
    def fake_run(cmd, *a, **kw):
        if "run" in cmd:
            return _completed(stdout=json.dumps({"job_id": JOB_ID}))
        return _completed(stdout=json.dumps({"status": "failed", "error": RAW_STDERR}))

    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    monkeypatch.setattr(tc.asyncio, "sleep", _instant_sleep)

    chat, msg = _Chat(), _Msg()
    asyncio.run(tc._dispatch_steer_and_report(chat, msg, "open the calendar"))

    reported = chat.last.edits + chat.sent
    assert reported, "no failure report sent"
    for text in reported:
        _assert_phone_safe(text)
        assert _items(text) <= 4, text


def test_progress_updates_do_not_echo_internal_strings(monkeypatch):
    """The third raw string: <code>{latest[:100]}</code>."""
    polls = {"n": 0}

    def fake_run(cmd, *a, **kw):
        if "run" in cmd:
            return _completed(stdout=json.dumps({"job_id": JOB_ID}))
        polls["n"] += 1
        if polls["n"] < 4:
            return _completed(stdout=json.dumps({
                "status": "running",
                "updates": ["exec_tool(read_file /srv/aos/core/steer/dispatch.py)"],
            }))
        return _completed(stdout=json.dumps({"status": "completed", "summary": "Done."}))

    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    monkeypatch.setattr(tc.asyncio, "sleep", _instant_sleep)

    chat, msg = _Chat(), _Msg()
    asyncio.run(tc._dispatch_steer_and_report(chat, msg, "open the calendar"))

    for text in chat.last.edits + chat.sent:
        _assert_phone_safe(text)


def test_no_raw_stderr_interpolation_remains_in_the_source():
    """Structural backstop: the four templates must not come back."""
    src = (BRIDGE / "telegram_channel.py").read_text()
    for banned in ("Failed to dispatch STEER job",
                   "<code>job: {job_id}</code>",
                   "{latest[:100]}",
                   "STEER dispatch error: {str(e)[:200]}"):
        assert banned not in src, f"raw template still present: {banned}"


async def _instant_sleep(_seconds):
    """Collapse the 5s poll interval so the suite stays fast."""
    return None
