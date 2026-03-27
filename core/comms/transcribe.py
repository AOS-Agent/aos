"""Voice message transcription — converts voice messages to text.

Uses the AOS transcriber service (Whisper large-v3-turbo at :7602).
Takes a Message with media_type="voice" and media_path set,
returns the transcript text.

Supports: .amr (iMessage), .opus (WhatsApp), .ogg, .m4a, .wav, .mp3

Usage:
    from core.comms.transcribe import transcribe_voice_message

    if msg.needs_transcription:
        transcript = transcribe_voice_message(msg)
        if transcript:
            msg.text = transcript
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

TRANSCRIBER_URL = "http://127.0.0.1:7602"
TIMEOUT = 120  # 2 minutes max per voice message


def is_transcriber_available() -> bool:
    """Check if the transcriber service is running."""
    try:
        req = Request(f"{TRANSCRIBER_URL}/health", method="GET")
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ready"
    except Exception:
        return False


def transcribe_voice_message(media_path: str, language_hint: str = "auto") -> str | None:
    """Transcribe a voice message file.

    Args:
        media_path: Absolute path to the audio file (.amr, .opus, .ogg, etc.)
        language_hint: "auto", "en", "ar", etc.

    Returns:
        Transcript text, or None if transcription failed.
    """
    if not media_path or not os.path.exists(media_path):
        log.debug(f"Voice file not found: {media_path}")
        return None

    # Check file size — skip very large files (>10MB)
    size = os.path.getsize(media_path)
    if size > 10 * 1024 * 1024:
        log.warning(f"Voice file too large ({size} bytes), skipping: {media_path}")
        return None

    if size == 0:
        return None

    try:
        payload = json.dumps({
            "audio_path": media_path,
            "mode": "fast",  # Fast mode for voice messages (short audio)
            "language_hint": language_hint,
            "timestamps": False,
        }).encode()

        req = Request(
            f"{TRANSCRIBER_URL}/transcribe",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=TIMEOUT) as resp:
            result = json.loads(resp.read())
            text = result.get("text", "").strip()
            if text:
                lang = result.get("language", "?")
                duration = result.get("duration_audio", 0)
                log.info(f"Transcribed voice ({duration:.0f}s, {lang}): {text[:60]}...")
                return text
            return None

    except URLError as e:
        log.warning(f"Transcriber service unavailable: {e}")
        return None
    except Exception as e:
        log.error(f"Transcription failed for {media_path}: {e}")
        return None


def transcribe_message_if_needed(msg) -> str:
    """Transcribe a Message object's voice attachment if needed.

    Returns the transcript text (also sets msg.text as side effect),
    or empty string if not a voice message or transcription failed.
    """
    if not msg.needs_transcription:
        return msg.text or ""

    if not msg.media_path:
        return ""

    transcript = transcribe_voice_message(msg.media_path)
    if transcript:
        msg.text = f"[voice] {transcript}"
        return msg.text

    return ""
