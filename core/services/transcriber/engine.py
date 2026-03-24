"""Transcription engine — single model, shared across all AOS pipelines.

Model: mlx-community/whisper-large-v3-turbo
  - 809M params, ~1.5GB on disk
  - 13-50x real-time on Apple Silicon
  - 99+ languages

Modes:
  - fast: single greedy pass (best_of=1), fastest
  - accurate: sample 5 candidates, pick best (best_of=5)
  - bilingual: dual-pass EN+AR, merge by segment quality — for voice messages
    with mid-sentence language switching

Whisper limitation: detects language from the first 30s and locks it for the
entire clip. For bilingual EN/AR content, we run two passes (one forced EN,
one forced AR) and merge segments by choosing whichever pass produced higher
confidence text for each time window.
"""

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The one model. Loaded once, used everywhere.
MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
_engine = None

# Arabic Unicode range for detecting Arabic script in text
_ARABIC_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')


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
        return _engine
    except ImportError:
        logger.error("mlx-whisper not installed. Run: uv pip install mlx-whisper")
        raise


def warmup():
    """Pre-load the model so first request is fast."""
    import tempfile
    import wave
    import struct

    _load_engine()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        with wave.open(f.name, "w") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))

        logger.info("Warming up model with silent audio...")
        t0 = time.monotonic()
        _engine.transcribe(f.name, path_or_hf_repo=MODEL_REPO)
        elapsed = time.monotonic() - t0
        logger.info(f"Model warm-up complete in {elapsed:.1f}s")


def _segment_quality(seg: dict) -> float:
    """Score a segment's quality. Higher = better.

    Uses avg_logprob and compression_ratio from Whisper's output.
    Penalizes hallucination patterns (repeated short phrases, empty text).
    """
    text = seg.get("text", "").strip()
    if not text:
        return -10.0

    avg_logprob = seg.get("avg_logprob", -1.0)
    compression = seg.get("compression_ratio", 1.0)
    no_speech = seg.get("no_speech_prob", 0.0)

    score = avg_logprob  # typically -0.2 to -1.0, higher is better

    # Penalize high compression (hallucination indicator)
    if compression > 2.4:
        score -= 1.0

    # Penalize high no-speech probability
    if no_speech > 0.6:
        score -= 2.0

    # Penalize suspiciously short repeated text
    if len(text) < 5 and text.endswith("?"):
        score -= 3.0

    return score


def _merge_bilingual(en_result: dict, ar_result: dict) -> tuple[list[dict], str]:
    """Merge English and Arabic transcription passes by segment quality.

    For each time window, pick whichever pass (EN or AR) produced the
    higher quality segment. This handles mid-sentence language switching.

    Returns:
        (merged_segments, detected_languages_summary)
    """
    en_segs = en_result.get("segments", [])
    ar_segs = ar_result.get("segments", [])

    if not en_segs and not ar_segs:
        return [], "unknown"
    if not en_segs:
        return [{"start": s["start"], "end": s["end"], "text": s["text"].strip(), "_lang": "ar"} for s in ar_segs], "ar"
    if not ar_segs:
        return [{"start": s["start"], "end": s["end"], "text": s["text"].strip(), "_lang": "en"} for s in en_segs], "en"

    # Build a timeline: for each EN segment, find overlapping AR segments
    # and pick the one with better quality
    merged = []
    ar_idx = 0

    for en_seg in en_segs:
        en_start = en_seg.get("start", 0)
        en_end = en_seg.get("end", 0)
        en_text = en_seg.get("text", "").strip()
        en_score = _segment_quality(en_seg)

        # Find overlapping AR segments
        best_ar_text = ""
        best_ar_score = -999
        ar_candidates = []

        for ar_seg in ar_segs:
            ar_start = ar_seg.get("start", 0)
            ar_end = ar_seg.get("end", 0)

            # Check overlap
            if ar_start < en_end and ar_end > en_start:
                ar_score = _segment_quality(ar_seg)
                ar_text = ar_seg.get("text", "").strip()
                ar_candidates.append((ar_text, ar_score))
                if ar_score > best_ar_score:
                    best_ar_score = ar_score
                    best_ar_text = ar_text

        # Decide: use EN or AR for this segment
        # Prefer AR if it contains Arabic script AND has reasonable quality
        ar_has_arabic = bool(_ARABIC_RE.search(best_ar_text)) if best_ar_text else False
        en_has_arabic = bool(_ARABIC_RE.search(en_text)) if en_text else False

        if ar_has_arabic and best_ar_score > -2.0:
            # AR pass captured Arabic content with decent quality — use it
            chosen_text = best_ar_text
            chosen_lang = "ar"
        elif en_has_arabic:
            # EN pass somehow got Arabic too — keep it
            chosen_text = en_text
            chosen_lang = "en+ar"
        else:
            # Default to EN for English content
            chosen_text = en_text
            chosen_lang = "en"

        if chosen_text:
            merged.append({
                "start": en_start,
                "end": en_end,
                "text": chosen_text,
                "_lang": chosen_lang,
            })

    # Determine overall language mix
    langs_used = set(s.get("_lang", "en") for s in merged)
    if "ar" in langs_used and "en" in langs_used:
        lang_summary = "en+ar"
    elif "ar" in langs_used:
        lang_summary = "ar"
    else:
        lang_summary = "en"

    return merged, lang_summary


def transcribe(
    audio_path: str,
    mode: str = "accurate",
    language_hint: str = "auto",
    timestamps: bool = True,
) -> TranscriptionResult:
    """Transcribe an audio file.

    Args:
        audio_path: Path to audio file (WAV, MP3, OGG, FLAC, etc.)
        mode: "fast", "accurate", or "bilingual" (dual-pass EN+AR merge)
        language_hint: "auto", "en", "ar", or any ISO 639-1 code
        timestamps: Include per-segment timestamps

    Returns:
        TranscriptionResult with text, language, segments, timing
    """
    engine = _load_engine()

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    t0 = time.monotonic()

    # Bilingual mode: run two passes and merge
    if mode == "bilingual":
        return _transcribe_bilingual(engine, audio_path, timestamps, t0)

    # Standard single-pass transcription
    kwargs = {
        "path_or_hf_repo": MODEL_REPO,
        "initial_prompt": "\u0628\u0633\u0645 \u0627\u0644\u0644\u0647 \u0627\u0644\u0631\u062d\u0645\u0646 \u0627\u0644\u0631\u062d\u064a\u0645. Hello, \u0645\u0631\u062d\u0628\u0627.",
        "condition_on_previous_text": True,
        "hallucination_silence_threshold": 1.0,
    }

    if mode == "fast":
        kwargs["best_of"] = 1
    else:
        kwargs["best_of"] = 5

    if language_hint != "auto":
        kwargs["language"] = language_hint

    if timestamps and mode == "accurate":
        kwargs["word_timestamps"] = True

    logger.info(f"Transcribing {audio_path} (mode={mode}, lang={language_hint})")

    result = engine.transcribe(audio_path, **kwargs)

    return _build_result(result, t0)


def _transcribe_bilingual(engine, audio_path: str, timestamps: bool, t0: float) -> TranscriptionResult:
    """Dual-pass bilingual transcription: EN pass + AR pass, merge by quality."""
    logger.info(f"Transcribing {audio_path} (mode=bilingual, dual-pass EN+AR)")

    base_kwargs = {
        "path_or_hf_repo": MODEL_REPO,
        "condition_on_previous_text": True,
        "hallucination_silence_threshold": 1.0,
        "best_of": 1,  # Greedy for speed (we're running twice)
    }

    # Pass 1: English
    en_kwargs = {**base_kwargs, "language": "en"}
    logger.info("  Pass 1/2: English...")
    en_result = engine.transcribe(audio_path, **en_kwargs)

    # Pass 2: Arabic
    ar_kwargs = {
        **base_kwargs,
        "language": "ar",
        "initial_prompt": "\u0628\u0633\u0645 \u0627\u0644\u0644\u0647 \u0627\u0644\u0631\u062d\u0645\u0646 \u0627\u0644\u0631\u062d\u064a\u0645",
    }
    logger.info("  Pass 2/2: Arabic...")
    ar_result = engine.transcribe(audio_path, **ar_kwargs)

    # Merge
    merged_segments, lang_summary = _merge_bilingual(en_result, ar_result)

    duration_processing = time.monotonic() - t0
    text = " ".join(seg["text"] for seg in merged_segments)
    duration_audio = merged_segments[-1]["end"] if merged_segments else 0

    # Clean segments for output (remove internal _lang key)
    clean_segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in merged_segments
    ]

    logger.info(
        f"Done (bilingual): {len(text)} chars, lang={lang_summary}, "
        f"audio={duration_audio:.1f}s, processing={duration_processing:.1f}s, "
        f"speed={duration_audio / max(duration_processing, 0.01):.1f}x RT, "
        f"en_segs={len(en_result.get('segments', []))}, ar_segs={len(ar_result.get('segments', []))}, "
        f"merged={len(merged_segments)}"
    )

    return TranscriptionResult(
        text=text,
        language=lang_summary,
        language_probability=0.0,
        segments=clean_segments,
        duration_audio=duration_audio,
        duration_processing=duration_processing,
        source="mlx-whisper-lv3t-bilingual",
    )


def _build_result(result: dict, t0: float) -> TranscriptionResult:
    """Convert raw mlx-whisper result to TranscriptionResult."""
    duration_processing = time.monotonic() - t0
    text = result.get("text", "").strip()
    language = result.get("language", "unknown")

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", "").strip(),
        })

    duration_audio = segments[-1]["end"] if segments else 0

    logger.info(
        f"Done: {len(text)} chars, lang={language}, "
        f"audio={duration_audio:.1f}s, processing={duration_processing:.1f}s, "
        f"speed={duration_audio / max(duration_processing, 0.01):.1f}x RT"
    )

    return TranscriptionResult(
        text=text,
        language=language,
        language_probability=0.0,
        segments=segments,
        duration_audio=duration_audio,
        duration_processing=duration_processing,
    )
