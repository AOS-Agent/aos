"""Long voice notes must not be dropped (aos#2357).

Two causes, both traced in the issue:

1. `voice_transcriber._transcribe_via_service` hardcoded `timeout=120` for the
   HTTP call to the transcriber service. Bilingual dual-pass runs slower than
   realtime on long notes (a 367s/6.1min note took 171s), so anything past
   ~4 minutes timed out even though the service would have finished — the
   bridge just stopped waiting and fell back to the slower per-request path.

2. `telegram_channel._handle_voice` echoed the transcript with a single
   unchunked `reply_text`. A transcript over Telegram's 4096-char limit threw
   `BadRequest: Message is too long` on both the primary send and its
   fallback, and the exception killed the handler before the prompt was ever
   dispatched to Claude — the note was silently lost.

Runs in the bridge venv:
    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import telegram_channel as tc  # noqa: E402
import voice_transcriber as vt  # noqa: E402

# ~9,000 chars — past Telegram's 4096 limit, matching the issue's report of a
# transcript that blew past the limit on a 6-minute (360s) note.
LONG_TRANSCRIPT = "word " * 1800


# ── voice_transcriber: timeout scales with duration ─────────────────────────

def test_service_timeout_floors_at_the_old_120s_for_short_notes():
    assert vt._service_timeout(10) == 120


def test_service_timeout_scales_to_three_times_duration():
    assert vt._service_timeout(360) >= 360 * 3


def test_service_timeout_is_capped_sensibly():
    huge = vt._service_timeout(100_000)
    assert huge < 100_000 * 3
    assert huge == vt._MAX_SERVICE_TIMEOUT


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_transcribe_via_service_passes_the_scaled_timeout_to_urlopen(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _FakeResponse({"text": "hello", "language": "en", "source": "service"})

    monkeypatch.setattr(vt, "urlopen", fake_urlopen)
    text = vt._transcribe_via_service("/tmp/x.wav", duration_s=360)

    assert text == "hello"
    assert captured["timeout"] >= 360 * 3


def test_transcribe_voice_forwards_duration_to_the_service_call(monkeypatch):
    calls = {}

    def fake_service(wav_path, duration_s=0):
        calls["duration_s"] = duration_s
        return LONG_TRANSCRIPT

    async def fake_convert(*a, **kw):
        return None

    monkeypatch.setattr(vt, "_transcribe_via_service", fake_service)
    monkeypatch.setattr(vt, "_convert_ogg_to_wav", lambda o, w: None)

    class FakeFile:
        async def download_to_drive(self, path):
            Path(path).write_bytes(b"")

    text = asyncio.run(vt.transcribe_voice(FakeFile(), duration_s=360))

    assert text == LONG_TRANSCRIPT
    assert calls["duration_s"] == 360


# ── telegram_channel: a long transcript must never crash the echo ──────────

class _Voice:
    def __init__(self, duration):
        self.duration = duration

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
    def __init__(self, chat_id=999, thread_id=None, duration=360):
        self.chat_id = chat_id
        self.voice = _Voice(duration)
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
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))
    ch = tc.TelegramChannel("token", 999)
    calls = {"transcribe_duration": None, "stream": []}

    async def fake_transcribe(_file, duration_s=0):
        calls["transcribe_duration"] = duration_s
        return LONG_TRANSCRIPT

    monkeypatch.setattr(tc, "transcribe_voice", fake_transcribe)

    async def fake_stream(self, chat, reply_to, message, user_key, **kwargs):
        calls["stream"].append(message)
        return "Here's what I caught. Shall I create them?"

    monkeypatch.setattr(tc.TelegramChannel, "_stream_response", fake_stream)

    def no_subprocess(cmd, *a, **kw):
        raise AssertionError(f"the voice path must not shell out: {cmd!r}")

    monkeypatch.setattr(tc.subprocess, "run", no_subprocess)

    return {"ch": ch, "calls": calls}


def _run_voice(ch, message):
    asyncio.run(ch._handle_voice(_Update(message), None))


def test_a_long_voice_note_reaches_claude_instead_of_dying_on_echo(channel):
    msg = _Message(duration=360)
    _run_voice(channel["ch"], msg)

    streamed = channel["calls"]["stream"]
    assert streamed, "the prompt never reached Claude — the handler died first"
    assert LONG_TRANSCRIPT in streamed[0]


def test_the_ramble_confirm_still_goes_out_for_a_long_note(channel):
    msg = _Message(duration=360)
    _run_voice(channel["ch"], msg)
    assert any("show you" in r for r in msg.replies), msg.replies


def test_no_single_send_exceeds_the_telegram_limit(channel):
    msg = _Message(duration=360)
    _run_voice(channel["ch"], msg)

    for r in msg.replies:
        assert len(r) <= 4096, f"reply_text sent {len(r)} chars"
    for s in msg.chat.sent:
        assert len(s) <= 4096, f"chat.send_message sent {len(s)} chars"


def test_the_full_transcript_still_reaches_the_operator_across_chunks(channel):
    """Nothing is dropped — a long transcript is chunked, not truncated."""
    import re

    msg = _Message(duration=360)
    _run_voice(channel["ch"], msg)

    echoed = "".join(r for r in msg.replies if "word" in r)
    plain = re.sub(r"</?i>", "", echoed)
    assert plain.replace(" ", "") == LONG_TRANSCRIPT.replace(" ", "")


def test_duration_from_voice_metadata_reaches_the_transcriber(channel):
    msg = _Message(duration=360)
    _run_voice(channel["ch"], msg)
    assert channel["calls"]["transcribe_duration"] == 360
