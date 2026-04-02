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

        # Streaming partial transcription — transcribe every N seconds during speech
        self._partial_interval_samples = int(sample_rate * 2.5)  # every 2.5s
        self._samples_since_partial = 0
        self._partial_id: str | None = None  # track the provisional segment ID

        # Silero VAD (preferred if available)
        self._silero_vad = None
        self._vad_frame_buffer = np.array([], dtype=np.float32)

        # STT engine
        self._stt_engine: str = "none"  # "parakeet", "whisper", or "none"
        self._stt_model = None
        self._stt_streamer = None

        # Audio recording to session WAV file
        self._recording = False
        self._wav_file = None
        self._recording_path: str | None = None
        self._recording_samples: int = 0

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

        # Write to session WAV if recording
        if self._recording and self._wav_file:
            try:
                pcm16 = (audio * 32767).astype(np.int16)
                self._wav_file.writeframes(pcm16.tobytes())
                self._recording_samples += len(audio)
            except Exception as e:
                logger.debug("WAV write failed: %s", e)

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
                self._samples_since_partial = 0
                self._partial_id = None
                await self._on_speech_start()

            if self._is_speaking:
                self._buffer.append(audio)
                self._buffer_samples += len(audio)
                self._samples_since_partial += len(audio)

                # Streaming partial: transcribe every 2.5s during speech
                if self._samples_since_partial >= self._partial_interval_samples:
                    self._samples_since_partial = 0
                    asyncio.create_task(self._emit_partial_transcript())
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
        logger.info("Speech started")
        self._partial_id = str(id(self._buffer))[:8]
        await self._emit_voice_state("listening")

    async def _emit_partial_transcript(self):
        """Transcribe the buffer so far and emit a provisional transcript.

        This gives the user live feedback while they're still speaking.
        The partial is marked as provisional — the frontend should update
        the same segment (by ID) rather than appending a new one.
        """
        if not self._buffer:
            return

        try:
            audio_so_far = np.concatenate(self._buffer)
            min_samples = int(self._sample_rate * 0.5)
            if len(audio_so_far) < min_samples:
                return

            text = ""
            if self._stt_engine == "whisper":
                text = await self._transcribe_whisper(audio_so_far)
            elif self._stt_engine == "parakeet":
                text = await self._transcribe_parakeet(audio_so_far)

            if text:
                duration = len(audio_so_far) / self._sample_rate
                logger.info("Partial transcript (%.1fs): %s", duration, text[:60])
                await self._emit_transcript(
                    text, duration,
                    is_provisional=True,
                    segment_id=self._partial_id,
                )
        except Exception as e:
            logger.debug("Partial transcription failed: %s", e)

    async def _on_speech_end(self):
        """Called when speech ends. Triggers final transcription."""
        self._is_speaking = False
        self._speech_frames = 0

        # Concatenate buffered audio
        if self._buffer:
            audio_data = np.concatenate(self._buffer)
        else:
            audio_data = np.array([], dtype=np.float32)

        self._buffer.clear()
        self._buffer_samples = 0
        self._samples_since_partial = 0

        # Reset Silero VAD state between utterances
        if self._silero_vad:
            self._silero_vad.reset()
            self._vad_frame_buffer = np.array([], dtype=np.float32)

        # Only transcribe if we have at least 0.5s of audio
        min_samples = int(self._sample_rate * 0.5)
        if len(audio_data) >= min_samples:
            await self._emit_voice_state("processing")
            await self._transcribe(audio_data)

        self._partial_id = None
        await self._emit_voice_state("idle")

    # ------------------------------------------------------------------
    # STT
    # ------------------------------------------------------------------

    async def _transcribe(self, audio: np.ndarray):
        """Transcribe audio to text using the best available engine."""
        text = ""
        duration = len(audio) / self._sample_rate

        try:
            if self._stt_engine == "parakeet":
                text = await asyncio.wait_for(self._transcribe_parakeet(audio), timeout=15.0)
            elif self._stt_engine == "whisper":
                text = await asyncio.wait_for(self._transcribe_whisper(audio), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("STT timed out after 15s")
            text = ""

        if not text:
            # Echo mode -- signal speech detected without transcription
            text = f"[Speech detected: {duration:.1f}s]"
            logger.info("Echo mode: %.1fs of speech", duration)

        # Emit FINAL transcript (replaces any provisional partial)
        await self._emit_transcript(
            text, duration,
            is_provisional=False,
            segment_id=self._partial_id,
        )

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
        """Signal the intelligence engine to process a voice transcript.

        The intelligence engine subscribes to transcript events on the
        EventBus and handles classification, card generation, and context
        assembly. This method emits a dedicated intelligence event so the
        engine can pick it up. If the engine isn't available, this is a
        harmless no-op — the transcript event already went out via
        _emit_transcript, so the data is captured regardless.
        """
        if not self._bus:
            return

        try:
            from qareen.events.types import Event

            await self._bus.emit(Event(
                event_type="voice.intelligence_request",
                source="voice",
                payload={
                    "text": text,
                    "speaker": "operator",
                    "timestamp": datetime.now().isoformat(),
                    "is_final": True,
                },
            ))
            logger.debug("Intelligence request emitted for voice transcript")
        except Exception as e:
            logger.debug("Intelligence event emission failed: %s", e)

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

    async def _emit_transcript(
        self,
        text: str,
        duration: float,
        is_provisional: bool = False,
        segment_id: str | None = None,
    ):
        """Emit a transcript event via EventBus -> SSE -> frontend.

        Args:
            text: The transcribed text.
            duration: Audio duration in seconds.
            is_provisional: If True, this is a streaming partial that may
                be updated. The frontend should replace the existing segment.
            segment_id: If provided, the frontend uses this to update an
                existing segment rather than creating a new one.
        """
        if not self._bus:
            return

        from qareen.events.types import Event

        await self._bus.emit(Event(
            event_type="transcript",
            source="voice",
            payload={
                "id": segment_id or str(id(text))[:8],
                "text": text,
                "speaker": "operator",
                "timestamp": datetime.now().isoformat(),
                "is_final": not is_provisional,
                "is_provisional": is_provisional,
                "is_update": is_provisional and segment_id is not None,
                "duration": round(duration, 2),
            },
        ))

    # ------------------------------------------------------------------
    # Audio recording to session WAV
    # ------------------------------------------------------------------

    def start_recording(self, audio_path: str) -> bool:
        """Start recording audio to a WAV file.

        Args:
            audio_path: Full path to the WAV file to write.

        Returns:
            True if recording started successfully.
        """
        if self._recording:
            logger.warning("Already recording to %s", self._recording_path)
            return False

        try:
            import wave

            # Ensure parent directory exists
            Path(audio_path).parent.mkdir(parents=True, exist_ok=True)

            wf = wave.open(audio_path, "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(self._sample_rate)

            self._wav_file = wf
            self._recording_path = audio_path
            self._recording_samples = 0
            self._recording = True
            logger.info("Audio recording started: %s", audio_path)
            return True
        except Exception as e:
            logger.error("Failed to start recording: %s", e)
            return False

    def stop_recording(self) -> tuple[str | None, float]:
        """Stop recording and close the WAV file.

        Returns:
            Tuple of (audio_path, duration_seconds). Path is None on failure.
        """
        if not self._recording:
            return None, 0.0

        self._recording = False
        audio_path = self._recording_path
        duration = self._recording_samples / self._sample_rate if self._recording_samples > 0 else 0.0

        try:
            if self._wav_file:
                self._wav_file.close()
                self._wav_file = None
            logger.info(
                "Audio recording stopped: %s (%.1fs, %d samples)",
                audio_path, duration, self._recording_samples,
            )
        except Exception as e:
            logger.error("Error closing WAV file: %s", e)
            audio_path = None

        self._recording_path = None
        self._recording_samples = 0
        return audio_path, duration

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
