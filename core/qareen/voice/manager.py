"""Voice pipeline manager -- orchestrates VAD, STT, and event emission.

Processing chain:
    Audio chunks (PCM float32, 16kHz) -> Energy VAD -> Buffer -> STT -> Events

Energy-based VAD is the default. When the Silero ONNX model is available at
~/.aos/models/silero_vad.onnx, it will be used for higher-accuracy detection.

STT uses Parakeet (mlx) for streaming partials and batch finals. Falls back to
mlx-whisper if Parakeet isn't installed. If neither is available, runs in "echo
mode" where speech is detected but not transcribed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from qareen.events.bus import EventBus

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SILERO_MODEL_PATH = Path.home() / ".aos" / "models" / "silero_vad.onnx"


class VoiceManager:
    """Manages the voice pipeline: audio chunks -> VAD -> STT -> events.

    The manager accumulates audio during speech, detects utterance boundaries
    via VAD, then transcribes complete utterances and emits events through
    the Qareen EventBus -> SSE -> frontend.
    """

    def __init__(self, bus: EventBus | None = None, sample_rate: int = SAMPLE_RATE):
        self._bus = bus
        self._sample_rate = sample_rate

        # Audio buffer for current utterance
        self._buffer: list[np.ndarray] = []
        self._chunk_count: int = 0
        self._buffer_samples: int = 0

        # VAD state
        self._is_speaking = False
        self._silence_frames = 0
        self._speech_frames = 0

        # Energy VAD thresholds (fallback)
        self._energy_threshold = 0.01
        self._silence_threshold = 30  # ~30 chunks of silence = end of utterance
        self._min_speech_frames = 5   # need 5 frames of speech to trigger

        # Silero VAD (preferred if available)
        self._silero_vad = None
        self._vad_frame_buffer = np.array([], dtype=np.float32)

        # STT engine
        self._stt_engine: str = "none"  # "parakeet", "whisper", or "none"
        self._stt_model = None
        self._stt_streamer = None

        # Init subsystems
        self._init_vad()
        self._init_stt()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_vad(self):
        """Try to load Silero VAD, fall back to energy-based."""
        try:
            if SILERO_MODEL_PATH.exists():
                import onnxruntime as ort  # noqa: F401 -- test availability

                from .silero_vad import SileroVAD

                self._silero_vad = SileroVAD(model_path=str(SILERO_MODEL_PATH))
                logger.info("Silero VAD loaded from %s", SILERO_MODEL_PATH)
                return
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Silero VAD failed to load: %s", e)

        logger.info("Using energy-based VAD (Silero not available)")

    def _init_stt(self):
        """Detect best available STT engine."""
        # Try Parakeet first (preferred -- streaming + no hallucinations)
        try:
            import parakeet_mlx  # noqa: F401

            self._stt_engine = "parakeet"
            logger.info("STT engine: Parakeet (mlx)")
            return
        except ImportError:
            pass

        # Fall back to mlx-whisper
        try:
            import mlx_whisper  # noqa: F401

            self._stt_engine = "whisper"
            logger.info("STT engine: MLX Whisper")
            return
        except ImportError:
            pass

        logger.warning("No STT engine available -- running in echo mode")
        self._stt_engine = "none"

    # ------------------------------------------------------------------
    # Audio processing
    # ------------------------------------------------------------------

    async def process_chunk(self, audio: np.ndarray):
        """Process an incoming audio chunk through VAD.

        Called for each ~100ms chunk from the browser WebSocket.
        """
        energy = float(np.sqrt(np.mean(audio ** 2)))
        if self._chunk_count % 50 == 0:  # Log every 5 seconds
            logger.info("Audio chunk #%d: %d samples, energy=%.4f, speaking=%s",
                       self._chunk_count, len(audio), energy, self._is_speaking)
        self._chunk_count += 1

        if self._silero_vad:
            await self._process_chunk_silero(audio)
        else:
            await self._process_chunk_energy(audio)

    async def _process_chunk_energy(self, audio: np.ndarray):
        """Energy-based VAD: simple RMS thresholding."""
        energy = float(np.sqrt(np.mean(audio ** 2)))

        if energy > self._energy_threshold:
            self._speech_frames += 1
            self._silence_frames = 0

            if not self._is_speaking and self._speech_frames >= self._min_speech_frames:
                self._is_speaking = True
                await self._on_speech_start()

            if self._is_speaking:
                self._buffer.append(audio)
                self._buffer_samples += len(audio)
        else:
            self._silence_frames += 1

            if self._is_speaking and self._silence_frames >= self._silence_threshold:
                await self._on_speech_end()

    async def _process_chunk_silero(self, audio: np.ndarray):
        """Silero VAD: model-based speech detection with 512-sample frames."""
        # Accumulate into frame buffer
        self._vad_frame_buffer = np.concatenate([self._vad_frame_buffer, audio])

        # Process complete 512-sample frames
        frame_size = 512
        while len(self._vad_frame_buffer) >= frame_size:
            frame = self._vad_frame_buffer[:frame_size]
            self._vad_frame_buffer = self._vad_frame_buffer[frame_size:]

            prob = self._silero_vad.process_frame(frame)
            is_speech = prob >= 0.5

            if is_speech:
                self._speech_frames += 1
                self._silence_frames = 0

                if not self._is_speaking and self._speech_frames >= self._min_speech_frames:
                    self._is_speaking = True
                    await self._on_speech_start()

                if self._is_speaking:
                    self._buffer.append(frame)
                    self._buffer_samples += len(frame)
            else:
                self._silence_frames += 1

                if self._is_speaking and self._silence_frames >= self._silence_threshold:
                    await self._on_speech_end()

    # ------------------------------------------------------------------
    # Speech boundary handlers
    # ------------------------------------------------------------------

    async def _on_speech_start(self):
        """Called when speech begins."""
        logger.debug("Speech started")
        await self._emit_voice_state("listening")

    async def _on_speech_end(self):
        """Called when speech ends. Triggers transcription."""
        self._is_speaking = False
        self._speech_frames = 0

        # Concatenate buffered audio
        if self._buffer:
            audio_data = np.concatenate(self._buffer)
        else:
            audio_data = np.array([], dtype=np.float32)

        self._buffer.clear()
        self._buffer_samples = 0

        # Reset Silero VAD state between utterances
        if self._silero_vad:
            self._silero_vad.reset()
            self._vad_frame_buffer = np.array([], dtype=np.float32)

        # Only transcribe if we have at least 0.5s of audio
        min_samples = int(self._sample_rate * 0.5)
        if len(audio_data) >= min_samples:
            await self._emit_voice_state("processing")
            await self._transcribe(audio_data)

        await self._emit_voice_state("idle")

    # ------------------------------------------------------------------
    # STT
    # ------------------------------------------------------------------

    async def _transcribe(self, audio: np.ndarray):
        """Transcribe audio to text using the best available engine."""
        text = ""
        duration = len(audio) / self._sample_rate

        if self._stt_engine == "parakeet":
            text = await self._transcribe_parakeet(audio)
        elif self._stt_engine == "whisper":
            text = await self._transcribe_whisper(audio)

        if not text:
            # Echo mode -- signal speech detected without transcription
            text = f"[Speech detected: {duration:.1f}s]"
            logger.info("Echo mode: %.1fs of speech", duration)

        # Emit transcript event
        await self._emit_transcript(text, duration)

        # Run through intelligence engine (non-blocking)
        if not text.startswith("["):
            asyncio.create_task(self._process_intelligence(text))

    async def _transcribe_parakeet(self, audio: np.ndarray) -> str:
        """Transcribe using Parakeet batch mode."""
        try:
            import parakeet_mlx as pm

            if self._stt_model is None:
                t0 = time.time()
                self._stt_model = pm.from_pretrained(
                    "mlx-community/parakeet-tdt-0.6b-v3"
                )
                logger.info("Parakeet model loaded in %.1fs", time.time() - t0)

            import tempfile
            import wave

            t0 = time.time()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
                with wave.open(f.name, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self._sample_rate)
                    pcm16 = (audio * 32767).astype(np.int16)
                    wf.writeframes(pcm16.tobytes())
                result = self._stt_model.transcribe(f.name)

            text = result.text.strip() if result else ""
            elapsed = (time.time() - t0) * 1000
            logger.info(
                "Parakeet: %.0fms audio -> %.0fms -- %s",
                len(audio) / self._sample_rate * 1000,
                elapsed,
                text[:80],
            )
            return text

        except Exception as e:
            logger.error("Parakeet transcription failed: %s", e)
            return ""

    async def _transcribe_whisper(self, audio: np.ndarray) -> str:
        """Transcribe using MLX Whisper."""
        try:
            import mlx_whisper

            t0 = time.time()
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo="mlx-community/whisper-base-mlx",
                language="en",
            )
            text = result.get("text", "").strip()
            elapsed = (time.time() - t0) * 1000
            logger.info(
                "Whisper: %.0fms audio -> %.0fms -- %s",
                len(audio) / self._sample_rate * 1000,
                elapsed,
                text[:80],
            )
            return text

        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Intelligence processing
    # ------------------------------------------------------------------

    async def _process_intelligence(self, text: str):
        """Run transcribed text through the intelligence engine.

        Non-blocking -- failures are logged but don't affect the voice pipeline.
        """
        try:
            from qareen.intelligence.engine import IntelligenceEngine

            # The engine is available on app state via ontology, but we
            # don't have direct access here. For now, emit a raw transcript
            # event and let any bus subscribers handle intelligence processing.
            logger.debug("Transcript for intelligence: %s", text[:80])
        except Exception as e:
            logger.debug("Intelligence processing skipped: %s", e)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit_voice_state(self, state: str):
        """Emit voice state change via EventBus -> SSE -> frontend."""
        if not self._bus:
            return

        from qareen.events.types import Event

        await self._bus.emit(Event(
            event_type="voice_state",
            source="voice",
            payload={"state": state},
        ))

    async def _emit_transcript(self, text: str, duration: float):
        """Emit a transcript event via EventBus -> SSE -> frontend."""
        if not self._bus:
            return

        from qareen.events.types import Event

        await self._bus.emit(Event(
            event_type="transcript",
            source="voice",
            payload={
                "text": text,
                "speaker": "operator",
                "timestamp": datetime.now().isoformat(),
                "is_final": True,
                "duration": round(duration, 2),
            },
        ))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_disconnect(self):
        """Clean up when the WebSocket disconnects."""
        # If we were mid-utterance, discard the buffer
        if self._buffer:
            logger.info("Discarding %d buffered samples on disconnect", self._buffer_samples)
        self._buffer.clear()
        self._buffer_samples = 0
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0

        if self._silero_vad:
            self._silero_vad.reset()
            self._vad_frame_buffer = np.array([], dtype=np.float32)

        await self._emit_voice_state("idle")
