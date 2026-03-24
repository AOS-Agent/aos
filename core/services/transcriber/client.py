"""Transcriber client — drop-in for any AOS pipeline that needs speech-to-text.

Usage:
    from client import transcribe, transcribe_file

    # Transcribe a local file
    result = transcribe("/tmp/audio.wav", mode="fast")
    print(result["text"])

    # With timestamps
    result = transcribe("/tmp/long_video.mp3", mode="accurate")
    print(result["timestamped_text"])

Falls back to direct mlx-whisper if the service is unreachable.
"""

import json
import logging
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

TRANSCRIBER_URL = "http://127.0.0.1:7601"
TIMEOUT = 300  # 5 minutes max for long audio


def transcribe(
    audio_path: str,
    mode: str = "accurate",
    language_hint: str = "auto",
    timestamps: bool = True,
) -> dict:
    """Transcribe an audio file via the transcriber service.

    Args:
        audio_path: Absolute path to audio file
        mode: "fast" or "accurate"
        language_hint: "auto", "en", "ar", or any ISO 639-1 code
        timestamps: Include per-segment timestamps

    Returns:
        dict with keys: text, language, segments, duration_audio,
        duration_processing, source, timestamped_text

    Raises:
        RuntimeError: If transcription fails entirely (service down + no fallback)
    """
    # Try the service first
    try:
        return _transcribe_via_service(audio_path, mode, language_hint, timestamps)
    except (URLError, ConnectionError, OSError) as e:
        logger.warning(f"Transcriber service unreachable: {e}. Trying direct mlx-whisper...")

    # Fallback: direct mlx-whisper import
    try:
        return _transcribe_direct(audio_path, mode, language_hint, timestamps)
    except ImportError:
        raise RuntimeError(
            "Transcriber service is down and mlx-whisper is not installed. "
            "Start the service: launchctl load ~/Library/LaunchAgents/com.aos.transcriber.plist"
        )


def is_service_running() -> bool:
    """Check if the transcriber service is reachable."""
    try:
        req = Request(f"{TRANSCRIBER_URL}/health", method="GET")
        with urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ready"
    except Exception:
        return False


def _transcribe_via_service(
    audio_path: str, mode: str, language_hint: str, timestamps: bool
) -> dict:
    """Call the transcriber HTTP service."""
    payload = json.dumps({
        "audio_path": audio_path,
        "mode": mode,
        "language_hint": language_hint,
        "timestamps": timestamps,
    }).encode()

    req = Request(
        f"{TRANSCRIBER_URL}/transcribe",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read())
        logger.info(
            f"Transcribed via service: {len(result.get('text', ''))} chars, "
            f"lang={result.get('language')}, "
            f"speed={result.get('duration_audio', 0) / max(result.get('duration_processing', 1), 0.01):.1f}x RT"
        )
        return result


def _transcribe_direct(
    audio_path: str, mode: str, language_hint: str, timestamps: bool
) -> dict:
    """Direct mlx-whisper transcription (fallback when service is down)."""
    import engine

    result = engine.transcribe(
        audio_path=audio_path,
        mode=mode,
        language_hint=language_hint,
        timestamps=timestamps,
    )
    d = result.to_dict()
    d["timestamped_text"] = result.timestamped_text
    return d
