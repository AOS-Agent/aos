"""One real inbound message must land in bridge.log.

The static gate (tests/test_bridge_logger_names.py, core suite) pins the logger
*names*. This is the end-to-end proof: drive one message through the real
`TelegramChannel._handle_message` with the same logger wiring `main.py` sets up,
pointed at a temp file, and assert the "Message:" and "Response:" lines are
actually written.

Runs in the bridge venv (it imports `telegram`), so it lives here rather than in
the core suite:

    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q

Everything with a side effect outside the process is stubbed — the in-flight
marker, the comms-bus queue file, the quick-command classifier (it shells out to
the work CLI), and the Claude stream. A test must never touch the live bridge's
runtime state.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent
REPO = BRIDGE.parent.parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import telegram_channel as tc  # noqa: E402


def _load_log_lib():
    """The same `lib.log` module main.py configures the bridge logger with."""
    path = REPO / "core" / "infra" / "lib" / "log.py"
    spec = importlib.util.spec_from_file_location("aos_log_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bridge_log(tmp_path):
    """Configure `aos.bridge` exactly as main.py does, at a temp log file."""
    log_lib = _load_log_lib()
    parent = logging.getLogger("aos.bridge")
    saved_handlers, saved_level, saved_prop = parent.handlers[:], parent.level, parent.propagate
    parent.handlers = []

    log_path = tmp_path / "bridge.log"
    log_lib.get_logger("bridge", log_file=str(log_path))
    try:
        yield log_path
    finally:
        for h in parent.handlers:
            h.close()
        parent.handlers, parent.level, parent.propagate = saved_handlers, saved_level, saved_prop


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id
        self.sent: list[str] = []

    async def send_message(self, text, **kwargs):
        self.sent.append(text)
        return None

    async def send_action(self, *_a, **_kw):
        return None


class _FakeMessage:
    def __init__(self, chat_id: int, text: str):
        self.chat_id = chat_id
        self.text = text
        self.chat = _FakeChat(chat_id)
        self.message_id = 1
        self.message_thread_id = None
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return None

    async def set_reaction(self, *_a, **_kw):
        return None


class _FakeUpdate:
    def __init__(self, message):
        self.message = message


class _DeadSession:
    alive = False


@pytest.fixture
def channel(monkeypatch):
    """A TelegramChannel with every external side effect stubbed out."""
    import session_manager

    ch = tc.TelegramChannel("token", 999)

    # Never write the live in-flight marker or the live comms-bus queue.
    monkeypatch.setattr(tc, "_save_inflight", lambda **kw: None)
    monkeypatch.setattr(tc, "_clear_inflight", lambda: None)
    monkeypatch.setattr(tc.TelegramChannel, "_queue_message_for_bus", lambda self, u: None)
    # Never consult real check-in state, and never shell out to the work CLI.
    monkeypatch.setattr(tc, "is_awaiting_checkin_reply", lambda: False)
    monkeypatch.setattr(tc, "classify_intent", lambda text: None)
    # Never spawn Claude.
    monkeypatch.setattr(session_manager, "get_persistent_session", lambda: _DeadSession())

    async def _fake_stream(self, chat, reply_to, message, user_key, **kwargs):
        return "All good — nothing needs you right now."

    monkeypatch.setattr(tc.TelegramChannel, "_stream_response", _fake_stream)
    return ch


def test_inbound_message_and_response_reach_the_log_file(channel, bridge_log):
    msg = _FakeMessage(999, "what is the weather like")
    asyncio.run(channel._handle_message(_FakeUpdate(msg), None))

    for h in logging.getLogger("aos.bridge").handlers:
        h.flush()

    contents = bridge_log.read_text()
    assert "Message:" in contents, (
        "no 'Message:' line in bridge.log — the module logger is not a child of "
        f"'aos.bridge'. Got:\n{contents}"
    )
    assert "Response:" in contents, f"no 'Response:' line in bridge.log. Got:\n{contents}"
    assert "what is the weather like" in contents


def test_module_logger_is_named_under_aos_bridge():
    assert tc.logger.name.startswith("aos.bridge."), tc.logger.name
