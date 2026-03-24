"""Voice message transcription — thin client to AOS Transcriber service.

All transcription runs through the shared transcriber at localhost:7601.
Model: Whisper Large V3 Turbo (809M params, 99+ languages, native EN/AR).

If the service is unreachable, falls back to direct mlx-whisper import.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

TRANSCRIBER_URL = "http://127.0.0.1:7601"

# Mode maps to transcriber service modes
_mode = "fast"
VALID_MODES = ("fast", "accurate")


def set_mode(mode: str):
    """Switch transcription mode."""
    global _mode
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode}. Use: {', '.join(VALID_MODES)}")
    _mode = mode
    logger.info(f"Transcription mode set to: {mode}")


def get_mode() -> str:
    return _mode


def _convert_ogg_to_wav(ogg_path: str, wav_path: str):
    """Convert OGG/OGA voice file to WAV using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")


def _transcribe_via_service(wav_path: str) -> str:
    """Call the shared transcriber service."""
    payload = json.dumps({
        "audio_path": wav_path,
        "mode": _mode,
        "language_hint": "auto",
        "timestamps": False,
    }).encode()

    req = Request(
        f"{TRANSCRIBER_URL}/transcribe",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())

    text = result.get("text", "").strip()
    lang = result.get("language", "unknown")
    source = result.get("source", "unknown")
    logger.info(f"transcriber ({source}, {lang}): {text[:100]}")
    return text


def _transcribe_fallback(wav_path: str) -> str:
    """Direct mlx-whisper fallback when service is down."""
    mlx_python = Path.home() / ".aos" / "services" / "transcriber" / ".venv" / "bin" / "python"

    if not mlx_python.exists():
        logger.error("No transcriber venv found")
        return "[transcription unavailable — transcriber service not running]"

    script = (
        'import mlx_whisper, json; '
        f'r = mlx_whisper.transcribe("{wav_path}", '
        'path_or_hf_repo="mlx-community/whisper-large-v3-turbo-mlx", '
        'initial_prompt="\\u0628\\u0633\\u0645 \\u0627\\u0644\\u0644\\u0647 \\u0627\\u0644\\u0631\\u062d\\u0645\\u0646 \\u0627\\u0644\\u0631\\u062d\\u064a\\u0645. Hello, \\u0645\\u0631\\u062d\\u0628\\u0627."); '
        'print(json.dumps({"text": r.get("text", ""), "language": r.get("language", "unknown")}))'
    )

    try:
        result = subprocess.run(
            [str(mlx_python), "-c", script],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            data = json.loads(result.stdout.strip())
            text = data.get("text", "").strip()
            lang = data.get("language", "unknown")
            logger.info(f"mlx-whisper fallback ({lang}): {text[:100]}")
            return text
        else:
            logger.error(f"mlx-whisper fallback failed: {result.stderr[:200]}")
            return "[transcription failed]"
    except Exception as e:
        logger.error(f"mlx-whisper fallback error: {e}")
        return "[transcription failed]"


async def transcribe_voice(voice_file) -> str:
    """Download and transcribe a Telegram voice message.

    Uses the shared transcriber service (Whisper Large V3 Turbo).
    Handles English, Arabic, and mid-sentence code-switching natively.

    Args:
        voice_file: telegram.File object from bot.get_file()

    Returns:
        Transcribed text string
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        ogg_path = f"{tmpdir}/voice.ogg"
        wav_path = f"{tmpdir}/voice.wav"

        await voice_file.download_to_drive(ogg_path)
        logger.info(f"Downloaded voice message ({Path(ogg_path).stat().st_size} bytes)")

        _convert_ogg_to_wav(ogg_path, wav_path)

        # Try service first, fall back to direct
        try:
            return _transcribe_via_service(wav_path)
        except (URLError, ConnectionError, OSError) as e:
            logger.warning(f"Transcriber service unreachable: {e}")
            try:
                from bridge_events import bridge_event
                bridge_event("transcriber_service_down", level="warning", error=str(e))
            except ImportError:
                pass
            return _transcribe_fallback(wav_path)
