"""Voice message transcription — auto-selects the best available backend.

Priority:
  1. mlx-whisper (Apple Silicon — fastest, uses Neural Engine)
  2. faster-whisper (CPU fallback — works everywhere)

The backend is auto-detected on first use. No configuration needed.
"""

import logging
import platform
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy-loaded model (only loaded on first use)
_model = None
_backend = None  # "mlx" or "faster"
_mode = "fast"

_MODE_MAP = {
    "fast": "base",
    "accurate": "small",
}
VALID_MODES = tuple(_MODE_MAP.keys())


def set_mode(mode: str):
    """Switch transcription mode. Reloads model on next transcription."""
    global _model, _mode
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode}. Use: {', '.join(VALID_MODES)}")
    if mode != _mode:
        _model = None
    _mode = mode
    logger.info(f"Whisper mode set to: {mode} ({_MODE_MAP[mode]} model)")


def get_mode() -> str:
    return _mode


def _get_model():
    """Lazy-load the best available whisper backend."""
    global _model, _backend
    if _model is not None:
        return _model

    model_name = _MODE_MAP[_mode]

    # Try mlx-whisper first (Apple Silicon only)
    if platform.machine() == "arm64":
        # Check the dedicated mlx-whisper venv
        mlx_python = Path.home() / ".aos" / "services" / "mlx-whisper" / ".venv" / "bin" / "python"
        if mlx_python.exists():
            try:
                # Verify mlx_whisper is importable in that venv
                result = subprocess.run(
                    [str(mlx_python), "-c", "import mlx_whisper; print('ok')"],
                    capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    _backend = "mlx"
                    logger.info(f"Using mlx-whisper ({model_name}) — Apple Silicon accelerated")
                    # mlx-whisper is used via subprocess, not imported directly
                    _model = "mlx"
                    return _model
            except Exception as e:
                logger.debug(f"mlx-whisper check failed: {e}")

        # Also try direct import (if installed in bridge venv)
        try:
            import mlx_whisper
            _backend = "mlx"
            _model = "mlx"
            logger.info(f"Using mlx-whisper ({model_name}) — Apple Silicon accelerated")
            return _model
        except ImportError:
            pass

    # Fall back to faster-whisper
    try:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper ({model_name})...")
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _backend = "faster"
        logger.info(f"faster-whisper ({model_name}) loaded")
        return _model
    except ImportError:
        logger.error("No whisper backend available. Install mlx-whisper or faster-whisper.")
        raise ImportError("No whisper backend available")


def _convert_ogg_to_wav(ogg_path: str, wav_path: str):
    """Convert OGG/OGA voice file to WAV using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")


async def transcribe_voice(voice_file) -> str:
    """Download and transcribe a Telegram voice message.

    Uses multilingual mode — no language is forced, so Whisper will
    detect and transcribe whatever languages are spoken (including
    mid-sentence code-switching between e.g. English and Arabic).

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

        _get_model()  # ensure backend is detected

        if _backend == "mlx":
            text = _transcribe_mlx(wav_path)
        else:
            text = _transcribe_faster(wav_path)

        return text


def _transcribe_mlx(wav_path: str) -> str:
    """Transcribe using mlx-whisper (Apple Silicon)."""
    model_name = _MODE_MAP[_mode]
    mlx_python = Path.home() / ".aos" / "services" / "mlx-whisper" / ".venv" / "bin" / "python"

    # Use the mlx-whisper venv's python to run transcription
    script = f"""
import mlx_whisper, json
result = mlx_whisper.transcribe("{wav_path}", path_or_hf_repo="mlx-community/whisper-{model_name}-mlx")
print(json.dumps({{"text": result.get("text", ""), "language": result.get("language", "unknown")}}))
"""
    try:
        result = subprocess.run(
            [str(mlx_python), "-c", script],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout.strip())
            text = data.get("text", "").strip()
            lang = data.get("language", "unknown")
            logger.info(f"mlx-whisper ({model_name}, {lang}): {text[:100]}")
            return text
        else:
            logger.error(f"mlx-whisper failed: {result.stderr[:200]}")
            from bridge_events import bridge_event
            bridge_event("mlx_whisper_failed", level="error",
                         stderr=result.stderr[:200], model=model_name)
            return _transcribe_faster(wav_path)
    except Exception as e:
        logger.error(f"mlx-whisper error: {e}")
        from bridge_events import bridge_event
        bridge_event("mlx_whisper_error", level="error",
                     error=str(e), model=model_name)
        return _transcribe_faster(wav_path)


def _transcribe_faster(wav_path: str) -> str:
    """Transcribe using faster-whisper (CPU fallback)."""
    global _model, _backend
    model_name = _MODE_MAP[_mode]

    # Load faster-whisper if not already loaded
    if _backend != "faster" or _model == "mlx":
        try:
            from faster_whisper import WhisperModel
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _backend = "faster"
        except ImportError:
            logger.error("No whisper backend available")
            return "[transcription unavailable — no whisper backend installed]"

    segments, info = _model.transcribe(
        wav_path,
        beam_size=5,
        language=None,
        multilingual=True,
        without_timestamps=True,
        condition_on_previous_text=True,
        language_detection_segments=4,
        initial_prompt="بسم الله الرحمن الرحيم. Hello, مرحبا.",
    )
    text = " ".join(segment.text.strip() for segment in segments)
    lang = info.language
    lang_prob = info.language_probability
    logger.info(f"faster-whisper ({model_name}, {lang} {lang_prob:.0%}): {text[:100]}")

    # Second pass for uncertain language
    if lang == "en" and lang_prob < 0.80:
        logger.info("Low English confidence — trying Arabic pass...")
        segments_ar, info_ar = _model.transcribe(
            wav_path, beam_size=5, language="ar",
            without_timestamps=True, condition_on_previous_text=True)
        text_ar = " ".join(seg.text.strip() for seg in segments_ar)
        if text_ar and text_ar.strip():
            text = text_ar

    return text
