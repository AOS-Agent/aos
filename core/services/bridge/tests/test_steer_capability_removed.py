"""aos#40 — the `steer` GUI-automation binary is dropped for good.

`vendor/` was never populated (`mac-mini-agent-tools` was never cloned —
confirmed empty but for `.gitkeep`), so `core/steer/dispatch.py`'s entire
job-dispatch mechanism (which also depended on the same missing vendor
tree's `Drive` tool) was already 100% non-functional. `core/steer/` is
removed rather than patched to pretend otherwise.

`_dispatch_steer_and_report` in telegram_channel.py — the bridge's caller —
is a *different* thing (see test_steer_report_copy.py's own docstring) and
is deliberately left untouched: its existing "dispatch_failed" branch
already produces safe, humanized copy whenever the subprocess it shells out
to fails for any reason. With core/steer/dispatch.py gone, that branch is
now simply the permanent, every-time outcome instead of an occasional one
— this test drives that with a REAL (unstubbed) subprocess.run against a
sandboxed $HOME whose ~/aos is a symlink to this repo, proving the actual
end-to-end production behavior once core/steer/ is deleted, not a mock of
it. The "dispatch_failed" copy itself (no retry promise) is pinned in
tests/test_notify_humanization.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import telegram_channel as tc  # noqa: E402


class _Msg:
    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        return self


class _Chat:
    async def send_message(self, text, **kwargs):
        return self


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))


def test_core_steer_package_is_gone_from_the_repo():
    assert not (REPO / "core" / "steer").exists()


def test_task_keywords_no_longer_advertise_steer_as_a_command():
    """"/steer" is no longer a recognized explicit command — a bare "/steer"
    with nothing else task-shaped after it is just chat, not a dead-tool
    dispatch. (A message that also happens to contain a generic keyword like
    "open " still dispatches, same as before — that's unrelated to whether
    /steer itself is advertised as a command.)"""
    assert not any("steer" in kw for kw in tc._TASK_KEYWORDS)
    assert not tc._is_task_dispatch("/steer check on that job")


def test_dispatch_fails_cleanly_with_no_stubbed_subprocess(tmp_path, monkeypatch):
    """No `monkeypatch.setattr(tc.subprocess, "run", ...)` here — this is the
    real subprocess.run, hitting the real (now-absent) dispatch script."""
    sandbox_home = tmp_path / "home"
    sandbox_home.mkdir()
    (sandbox_home / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: sandbox_home))

    dispatch_script = sandbox_home / "aos" / "core" / "steer" / "dispatch.py"
    assert not dispatch_script.exists(), "test setup assumption: core/steer/ is gone"

    chat, msg = _Chat(), _Msg()
    asyncio.run(tc._dispatch_steer_and_report(chat, msg, "open the calendar"))

    assert msg.sent, "operator must still get a reply, not silence"
    text = msg.sent[0]
    assert "available" in text.lower()
    assert "try again" not in text.lower()
    assert "Traceback" not in text
    assert "steer" not in text.lower(), "never names the dead internal tool to the operator"
    assert str(dispatch_script) not in text
