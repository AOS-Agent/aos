"""A voice note is a ramble, not a chat message (aos#235).

`core/skills/ramble/SKILL.md` has said "Also called by bridge after voice note
transcription" since it was written. `_handle_voice` did no such thing: it
echoed the transcript in italics and piped it into the same generic chat path as
typed text, so whether ramble engaged at all depended on Claude noticing its own
trigger phrases inside one open-ended session. No forced routing, no confirm
step, no guarantee anything was captured.

The operator's call: voice notes force-route through ramble, with a one-line
confirm back before anything is created. Phase 1 wires the routing and the
confirm; the skill itself is unchanged (it already gates task creation behind
"Ready to commit?").

Runs in the bridge venv:
    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import telegram_channel as tc  # noqa: E402

TRANSCRIPT = ("I need to fix the login page this week, and I keep thinking about "
              "running a winter retreat")


class _Voice:
    duration = 14

    async def get_file(self):
        return object()


class _Chat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.sent: list[str] = []

    async def send_message(self, text, **kwargs):
        self.sent.append(text)
        return None

    async def send_action(self, *a, **kw):
        return None


class _Message:
    def __init__(self, chat_id=999, thread_id=None):
        self.chat_id = chat_id
        self.voice = _Voice()
        self.chat = _Chat(chat_id)
        self.message_thread_id = thread_id
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return None

    async def set_reaction(self, *a, **kw):
        return None


class _Update:
    def __init__(self, message):
        self.message = message


@pytest.fixture
def channel(monkeypatch, tmp_path):
    """A channel whose only observable effects are the recorded calls."""
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))
    ch = tc.TelegramChannel("token", 999)

    calls: list[tuple[str, str]] = []

    async def fake_transcribe(_file):
        return TRANSCRIPT

    monkeypatch.setattr(tc, "transcribe_voice", fake_transcribe)

    async def fake_stream(self, chat, reply_to, message, user_key, **kwargs):
        calls.append(("stream", message))
        return "Here's what I caught — three tasks and an idea. Shall I create them?"

    monkeypatch.setattr(tc.TelegramChannel, "_stream_response", fake_stream)

    # Tripwire: nothing in this path may reach the work CLI. Task creation is the
    # ramble skill's job, after the operator approves.
    def no_subprocess(cmd, *a, **kw):
        raise AssertionError(f"the voice path must not shell out: {cmd!r}")

    monkeypatch.setattr(tc.subprocess, "run", no_subprocess)

    return {"ch": ch, "calls": calls}


def _run_voice(ch, message):
    asyncio.run(ch._handle_voice(_Update(message), None))


# ── The done-when ───────────────────────────────────────────────────────────

def test_voice_note_is_routed_through_ramble(channel):
    msg = _Message()
    _run_voice(channel["ch"], msg)

    streamed = [m for kind, m in channel["calls"] if kind == "stream"]
    assert streamed, "nothing was dispatched"
    assert "ramble" in streamed[0].lower(), streamed[0]
    assert TRANSCRIPT in streamed[0], "the transcript itself has to go with it"


def test_a_confirm_goes_out_before_anything_is_created(channel):
    msg = _Message()
    _run_voice(channel["ch"], msg)

    confirms = [r for r in msg.replies if "before anything" in r or "show you" in r]
    assert confirms, f"no confirm message sent. replies: {msg.replies}"

    # Ordering: the confirm must be on the operator's phone before the session
    # that could create things is even started.
    confirm_index = msg.replies.index(confirms[0])
    transcript_index = next(i for i, r in enumerate(msg.replies) if TRANSCRIPT in r)
    assert transcript_index < confirm_index, "echo the transcript, then confirm"
    assert channel["calls"], "the ramble dispatch never happened"


def test_the_confirm_is_one_line_and_house_style(channel):
    msg = _Message()
    _run_voice(channel["ch"], msg)
    confirm = next(r for r in msg.replies if "show you" in r)
    assert confirm.count("\n") == 0, "one line"
    assert len(confirm) <= 160
    assert "/" not in confirm, "no paths or slash commands in operator-facing copy"
    assert "ramble" not in confirm.lower(), "skill names are our vocabulary, not theirs"


def test_the_transcript_is_still_echoed(channel):
    msg = _Message()
    _run_voice(channel["ch"], msg)
    assert any(TRANSCRIPT in r for r in msg.replies)


def test_the_voice_note_is_recorded_in_the_store(channel, tmp_path):
    import sqlite3

    msg = _Message()
    _run_voice(channel["ch"], msg)

    db = tmp_path / "bridge.db"
    rows = sqlite3.connect(str(db)).execute(
        "SELECT direction, kind FROM messages ORDER BY id").fetchall()
    assert ("in", "voice") in rows


# ── The one case that is not a ramble ───────────────────────────────────────

def test_a_voice_note_in_a_project_topic_still_goes_to_that_agent(channel):
    """A voice note in the nuchay topic is for nuchay, not for a brain dump."""
    ch = channel["ch"]
    ch.topic_routes = {77: {"cwd": str(Path.home()), "agent": "nuchay"}}
    msg = _Message(thread_id=77)
    _run_voice(ch, msg)

    streamed = [m for kind, m in channel["calls"] if kind == "stream"]
    assert streamed
    assert streamed[0].startswith("ask nuchay to"), streamed[0]
    assert "ramble" not in streamed[0].lower()
    assert not [r for r in msg.replies if "show you" in r], \
        "no ramble confirm on the project-topic path"


# ── Failure modes keep their old behaviour ──────────────────────────────────

def test_an_empty_transcription_does_not_start_a_ramble(channel, monkeypatch):
    async def empty(_file):
        return "   "

    monkeypatch.setattr(tc, "transcribe_voice", empty)
    msg = _Message()
    _run_voice(channel["ch"], msg)
    assert channel["calls"] == []
    assert any("transcribe" in r for r in msg.replies)


def test_a_failed_transcription_does_not_start_a_ramble(channel, monkeypatch):
    async def boom(_file):
        raise RuntimeError("whisper died")

    monkeypatch.setattr(tc, "transcribe_voice", boom)
    msg = _Message()
    _run_voice(channel["ch"], msg)
    assert channel["calls"] == []
