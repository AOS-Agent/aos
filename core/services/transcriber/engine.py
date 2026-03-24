"""Transcription engine — single model, shared across all AOS pipelines.

Model: mlx-community/whisper-large-v3-turbo
  - 809M params, ~1.5GB on disk
  - 13-50x real-time on Apple Silicon
  - 99+ languages, native English/Arabic code-switching
  - No language forcing needed — auto-detects and handles bilingual

Modes:
  - fast: greedy decoding, no beam search (~2x faster)
  - accurate: beam_size=5, condition_on_previous_text (~20% better on long audio)
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The one model. Loaded once, used everywhere.
MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
_engine = None


class TranscriptionResult:
    """Result from a transcription."""

    __slots__ = ("text", "language", "language_probability", "segments",
                 "duration_audio", "duration_processing", "source")

    def __init__(self, text: str, language: str, language_probability: float,
                 segments: list[dict], duration_audio: float,
                 duration_processing: float, source: str = "mlx-whisper-lv3t"):
        self.text = text
        self.language = language
        self.language_probability = language_probability
        self.segments = segments
        self.duration_audio = duration_audio
        self.duration_processing = duration_processing
        self.source = source

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "language_probability": self.language_probability,
            "segments": self.segments,
            "duration_audio": self.duration_audio,
            "duration_processing": self.duration_processing,
            "source": self.source,
        }

    @property
    def timestamped_text(self) -> str:
        """Text with timestamps from segments."""
        if not self.segments:
            return self.text
        lines = []
        for seg in self.segments:
            start = seg.get("start", 0)
            minutes = int(start // 60)
            seconds = start % 60
            lines.append(f"[{minutes:02d}:{seconds:05.2f}] {seg.get('text', '').strip()}")
        return "\n".join(lines)


def _load_engine():
    """Lazy-load mlx-whisper. Called once on first request."""
    global _engine
    if _engine is not None:
        return _engine

    try:
        import mlx_whisper
        _engine = mlx_whisper
        logger.info(f"mlx-whisper loaded, model repo: {MODEL_REPO}")

        # Warm up — first transcription triggers model download if needed.
        # The HuggingFace cache handles this automatically via ~/.cache/huggingface/
        return _engine
    except ImportError:
        logger.error("mlx-whisper not installed. Run: uv pip install mlx-whisper")
        raise


def warmup():
    """Pre-load the model so first request is fast.

    Call this at service startup. Downloads model if not cached.
    """
    import tempfile
    import wave
    import struct

    _load_engine()

    # Generate a tiny silent WAV to force model load
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        with wave.open(f.name, "w") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            # 0.5 seconds of silence
            wav.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))

        logger.info("Warming up model with silent audio...")
        t0 = time.monotonic()
        _engine.transcribe(f.name, path_or_hf_repo=MODEL_REPO)
        elapsed = time.monotonic() - t0
        logger.info(f"Model warm-up complete in {elapsed:.1f}s")


def transcribe(
    audio_path: str,
    mode: str = "accurate",
    language_hint: str = "auto",
    timestamps: bool = True,
) -> TranscriptionResult:
    """Transcribe an audio file.

    Args:
        audio_path: Path to audio file (WAV, MP3, OGG, FLAC, etc.)
        mode: "fast" (greedy) or "accurate" (beam search)
        language_hint: "auto", "en", "ar", or any ISO 639-1 code
        timestamps: Include per-segment timestamps

    Returns:
        TranscriptionResult with text, language, segments, timing
    """
    engine = _load_engine()

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Build transcription kwargs
    kwargs = {
        "path_or_hf_repo": MODEL_REPO,
        # Bilingual initial prompt — biases Whisper toward detecting both
        # English and Arabic without forcing either language.
        "initial_prompt": "\u0628\u0633\u0645 \u0627\u0644\u0644\u0647 \u0627\u0644\u0631\u062d\u0645\u0646 \u0627\u0644\u0631\u062d\u064a\u0645. Hello, \u0645\u0631\u062d\u0628\u0627.",
        "condition_on_previous_text": True,
    }

    # Mode selection
    if mode == "fast":
        kwargs["beam_size"] = 1
        kwargs["best_of"] = 1
    else:
        kwargs["beam_size"] = 5

    # Language hint
    if language_hint != "auto":
        kwargs["language"] = language_hint

    # Word timestamps for accurate mode
    if timestamps and mode == "accurate":
        kwargs["word_timestamps"] = True

    logger.info(f"Transcribing {audio_path} (mode={mode}, lang={language_hint})")
    t0 = time.monotonic()

    result = engine.transcribe(audio_path, **kwargs)

    duration_processing = time.monotonic() - t0
    text = result.get("text", "").strip()
    language = result.get("language", "unknown")

    # Extract segments
    segments = []
    raw_segments = result.get("segments", [])
    for seg in raw_segments:
        segments.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", "").strip(),
        })

    # Estimate audio duration from last segment end
    duration_audio = segments[-1]["end"] if segments else 0

    logger.info(
        f"Done: {len(text)} chars, lang={language}, "
        f"audio={duration_audio:.1f}s, processing={duration_processing:.1f}s, "
        f"speed={duration_audio / duration_processing:.1f}x RT"
    )

    return TranscriptionResult(
        text=text,
        language=language,
        language_probability=0.0,  # mlx-whisper doesn't expose this directly
        segments=segments,
        duration_audio=duration_audio,
        duration_processing=duration_processing,
    )
