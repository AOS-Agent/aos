"""Voice message transcription — thin client to AOS Transcriber service.

All transcription runs through the shared transcriber at localhost:7602.
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

logger = logging.getLogger("aos.bridge.voice_transcriber")

# Must match `port:` in core/services/transcriber/service.yaml. :7601 is
# whatsmeow — posting audio there silently fell through to the slow per-request
# mlx-whisper fallback while the preloaded model sat idle (aos#180).
TRANSCRIBER_URL = "http://127.0.0.1:7602"

# Mode maps to transcriber service modes
_mode = "fast"
VALID_MODES = ("fast", "accurate")

# Bilingual dual-pass runs slower than realtime on long notes (aos#2357: a
# 367s/6.1min note took 171s to transcribe — well past the old flat 120s
# timeout, which gave up on a call the service would have finished, and fell
# back to the much slower per-request mlx-whisper path). Scale the wait with
# the note's own duration instead: floor at the old 120s so short notes are
# unaffected, cap so a corrupt or absurdly long file can't hang the bridge
# indefinitely.
_MIN_SERVICE_TIMEOUT = 120
_MAX_SERVICE_TIMEOUT = 1200  # 20 minutes


def _service_timeout(duration_s: float) -> int:
    """HTTP timeout (seconds) for the transcriber service call, scaled to
    audio length: max(120, 3x duration), capped at 20 minutes."""
    return min(_MAX_SERVICE_TIMEOUT, max(_MIN_SERVICE_TIMEOUT, int(duration_s * 3)))


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


def _transcribe_via_service(wav_path: str, duration_s: float = 0) -> str:
    """Call the shared transcriber service.

    Uses bilingual mode by default — dual-pass EN+AR merge for voice
    messages where the operator switches languages mid-sentence.

    `duration_s` (the note's own length, from Telegram voice metadata) scales
    the HTTP timeout — see `_service_timeout` (aos#2357).
    """
    # Voice messages use bilingual mode (dual-pass EN+AR) unless
    # operator explicitly set a different mode via /whisper
    effective_mode = "bilingual" if _mode == "fast" else _mode
    payload = json.dumps({
        "audio_path": wav_path,
        "mode": effective_mode,
        "language_hint": "auto",
        "timestamps": False,
    }).encode()

    req = Request(
        f"{TRANSCRIBER_URL}/transcribe",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(req, timeout=_service_timeout(duration_s)) as resp:
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
        'path_or_hf_repo="mlx-community/whisper-large-v3-turbo", '
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


async def transcribe_voice(voice_file, duration_s: float = 0) -> str:
    """Download and transcribe a Telegram voice message.

    Uses the shared transcriber service (Whisper Large V3 Turbo).
    Handles English, Arabic, and mid-sentence code-switching natively.

    Args:
        voice_file: telegram.File object from bot.get_file()
        duration_s: audio duration in seconds, from Telegram voice metadata
            (``update.message.voice.duration``). Scales the service timeout
            so long notes aren't dropped (aos#2357).

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
            return _transcribe_via_service(wav_path, duration_s)
        except (URLError, ConnectionError, OSError) as e:
            logger.warning(f"Transcriber service unreachable: {e}")
            try:
                from bridge_events import bridge_event
                bridge_event("transcriber_service_down", level="warning", error=str(e))
            except ImportError:
                pass
            return _transcribe_fallback(wav_path)
