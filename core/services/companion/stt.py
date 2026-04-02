"""Hybrid Speech-to-Text — Parakeet streaming + Whisper finals.

Parakeet TDT 0.6B v3: streaming partials (~100ms/chunk), zero hallucinations.
Whisper large-v3-turbo: accurate finals (~800ms/utterance), 2.2% WER.

Best of both: live text as you speak, accurate permanent record when you stop.
"""

import logging
import tempfile
import time
import wave

import numpy as np

log = logging.getLogger("companion.stt")

PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
SAMPLE_RATE = 16000

# Known Whisper hallucination patterns
HALLUCINATION_PATTERNS = [
    "thank you for watching", "subscribe to my channel",
    "please like and subscribe", "thanks for watching",
    "subtitles by", "copyright", "all rights reserved",
]


class SpeechToText:
    """Hybrid STT: Parakeet for streaming, Whisper for accurate finals."""

    def __init__(self, parakeet_model: str = PARAKEET_MODEL, whisper_model: str = WHISPER_MODEL):
        self._parakeet_id = parakeet_model
        self._whisper_id = whisper_model
        self._parakeet = None
        self._streamer = None
        self._whisper_loaded = False
        self._loaded = False

    def load(self):
        """Load the Parakeet model for streaming. Whisper loads lazily on first final."""
        import parakeet_mlx as pm

        t0 = time.time()
        self._parakeet = pm.from_pretrained(self._parakeet_id)
        self._loaded = True
        log.info("Parakeet loaded: %s (%.1fs)", self._parakeet_id, time.time() - t0)

    def _ensure_whisper(self):
        """Lazy-load Whisper on first final transcription."""
        if not self._whisper_loaded:
            import mlx_whisper
            t0 = time.time()
            # Warm up whisper by transcribing silence
            silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
            mlx_whisper.transcribe(silence, path_or_hf_repo=self._whisper_id, verbose=False)
            self._whisper_loaded = True
            log.info("Whisper loaded: %s (%.1fs)", self._whisper_id, time.time() - t0)

    def new_stream(self):
        """Create a new streaming session. Call at start of each utterance/meeting."""
        import parakeet_mlx as pm

        if not self._loaded:
            self.load()
        self._streamer = pm.StreamingParakeet(self._parakeet, context_size=(70, 10))
        log.debug("New streaming session created")

    def feed_chunk(self, audio: np.ndarray) -> str | None:
        """Feed a 1-second audio chunk to the streamer. Returns current partial text.

        Call this every ~1 second with new audio. The streamer accumulates
        context and returns the best transcription so far.
        """
        import mlx.core as mx

        if self._streamer is None:
            self.new_stream()

        chunk = mx.array(audio.flatten().astype(np.float32))
        self._streamer.add_audio(chunk)

        result = self._streamer.result
        if result and result.text.strip():
            return result.text.strip()
        return None

    def finalize_stream(self) -> str | None:
        """Finalize the current stream and return the complete text.

        Call when speech ends. Resets the streamer for the next utterance.
        """
        if self._streamer is None:
            return None

        result = self._streamer.result
        text = result.text.strip() if result else None
        self._streamer = None  # Reset for next utterance
        return text

    def transcribe(self, audio: np.ndarray, language: str = "en") -> dict | None:
        """Batch transcribe using Parakeet — accurate, fast, no hallucinations.

        Called once per utterance when speech ends. ~200ms for 10s audio on M4.
        Parakeet batch produces clean punctuated text with zero hallucinations.
        """
        if not self._loaded:
            self.load()

        audio = audio.flatten().astype(np.float32)

        if len(audio) < SAMPLE_RATE * 0.3:
            return None

        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            with wave.open(f.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            result = self._parakeet.transcribe(f.name)

        elapsed_ms = (time.time() - t0) * 1000
        audio_duration_ms = len(audio) / SAMPLE_RATE * 1000
        text = result.text.strip() if result else ""

        if not text:
            return None

        log.info(
            "STT: %.0fms audio → %.0fms — %s",
            audio_duration_ms, elapsed_ms, text[:80],
        )

        return {
            "text": text,
            "language": language,
            "segments": [],
            "inference_ms": elapsed_ms,
            "audio_ms": audio_duration_ms,
        }

    def transcribe_fast(self, audio: np.ndarray) -> dict | None:
        """Fast batch transcription using Parakeet. Used when speed matters more than accuracy."""
        if not self._loaded:
            self.load()

        audio = audio.flatten().astype(np.float32)
        if len(audio) < SAMPLE_RATE * 0.3:
            return None

        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            with wave.open(f.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            result = self._parakeet.transcribe(f.name)

        elapsed_ms = (time.time() - t0) * 1000
        text = result.text.strip() if result else ""
        if not text:
            return None

        log.info("STT fast (Parakeet): %.0fms audio → %.0fms — %s",
                 len(audio) / SAMPLE_RATE * 1000, elapsed_ms, text[:80])

        return {"text": text, "inference_ms": elapsed_ms, "audio_ms": len(audio) / SAMPLE_RATE * 1000}
