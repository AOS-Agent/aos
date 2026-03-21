"""Voice message transcription — local Whisper via faster-whisper.

Two modes:
  - "fast" (base model, multilingual, ~150MB) — default
  - "accurate" (small model, multilingual, ~500MB) — better for mixed languages

Switch via /whisper fast | /whisper accurate in Telegram.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy-loaded model (only loaded on first use, reloaded on mode change)
_model = None
_mode = "fast"  # "fast" or "accurate" — default to fast for low latency

# Map mode → Whisper model name
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
        _model = None  # force reload
    _mode = mode
    logger.info(f"Whisper mode set to: {mode} ({_MODE_MAP[mode]} model)")


def get_mode() -> str:
    return _mode


def _get_model():
    """Lazy-load the faster-whisper model."""
    global _model
    if _model is None:
        model_name = _MODE_MAP[_mode]
        logger.info(f"Loading faster-whisper model ({model_name})... this may take a moment")
        from faster_whisper import WhisperModel
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        logger.info(f"faster-whisper model ({model_name}) loaded")
    return _model


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

        model = _get_model()

        # First pass: auto-detect language
        segments, info = model.transcribe(
            wav_path,
            beam_size=5,
            language=None,
            multilingual=True,
            without_timestamps=True,
            condition_on_previous_text=True,
            language_detection_segments=4,
            # Prime the model to expect both English and Arabic script.
            # This well-known trick makes Whisper output Arabic script
            # instead of transliterating to English.
            initial_prompt="بسم الله الرحمن الرحيم. Hello, مرحبا.",
        )
        text = " ".join(segment.text.strip() for segment in segments)
        model_name = _MODE_MAP[_mode]
        lang = info.language
        lang_prob = info.language_probability
        logger.info(f"Transcription ({_mode}/{model_name}, {lang} {lang_prob:.0%}, {info.duration:.1f}s): {text[:100]}")

        # If language detection was uncertain (< 80%), do a second pass
        # forcing Arabic to see if we get a better result
        if lang == "en" and lang_prob < 0.80:
            logger.info("Low English confidence — trying Arabic pass...")
            segments_ar, info_ar = model.transcribe(
                wav_path,
                beam_size=5,
                language="ar",
                without_timestamps=True,
                condition_on_previous_text=True,
            )
            text_ar = " ".join(seg.text.strip() for seg in segments_ar)
            logger.info(f"Arabic pass: {text_ar[:100]}")
            # Combine: return the Arabic pass if it produced meaningful output
            if text_ar and text_ar.strip():
                text = text_ar

        return text
