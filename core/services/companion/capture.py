"""Audio capture — VAD-driven streaming for live transcription.

Replaces the old fixed 5-second chunking approach with voice-activity-gated
speech segments. Audio frames flow continuously through the VAD; only actual
speech gets emitted for transcription. Silent frames are discarded (saving
compute and preventing Whisper hallucinations).

State machine:
  IDLE → SPEAKING → TRAILING_SILENCE → (emit segment) → IDLE
"""

import asyncio
import logging
import time
import wave
from enum import Enum
from pathlib import Path

import numpy as np

from vad import VoiceActivityDetector, FRAME_SIZE, SAMPLE_RATE

log = logging.getLogger("companion.capture")

CHANNELS = 1
DTYPE = "float32"

# VAD state machine parameters
PRE_SPEECH_BUFFER_MS = 300       # Keep 300ms before VAD trigger (catches speech onset)
TRAILING_SILENCE_MS = 600        # Silence after speech to confirm end of utterance
MAX_SEGMENT_DURATION_S = 15      # Force-emit if someone talks continuously for 15s
MIN_SEGMENT_DURATION_MS = 300    # Discard segments shorter than 300ms (clicks, pops)


class CaptureState(str, Enum):
    IDLE = "idle"              # No speech detected, buffering pre-speech frames
    SPEAKING = "speaking"      # Speech active, accumulating frames
    TRAILING = "trailing"      # Silence after speech, waiting to confirm end


class SpeechSegment:
    """A detected speech segment with audio and metadata."""
    __slots__ = ("audio", "duration_ms", "start_time", "rms")

    def __init__(self, audio: np.ndarray, start_time: float):
        self.audio = audio
        self.duration_ms = len(audio) / SAMPLE_RATE * 1000
        self.start_time = start_time
        self.rms = float(np.sqrt(np.mean(audio ** 2)))


class AudioCapture:
    """VAD-driven audio capture. Emits SpeechSegment objects via async queue."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._stream = None
        self._running = False

        # Queues
        self._segment_queue: asyncio.Queue | None = None
        self._level_queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # VAD
        self._vad = VoiceActivityDetector(threshold=0.5)

        # State machine
        self._state = CaptureState.IDLE
        self._speech_frames: list[np.ndarray] = []
        self._pre_buffer: list[np.ndarray] = []  # rolling buffer of recent frames
        self._pre_buffer_max = int(PRE_SPEECH_BUFFER_MS / 1000 * sample_rate / FRAME_SIZE)
        self._trailing_silence_frames = 0
        self._trailing_silence_max = int(TRAILING_SILENCE_MS / 1000 * sample_rate / FRAME_SIZE)
        self._max_segment_frames = int(MAX_SEGMENT_DURATION_S * sample_rate / FRAME_SIZE)
        self._min_segment_samples = int(MIN_SEGMENT_DURATION_MS / 1000 * sample_rate)
        self._speech_start_time = 0.0
        self._meeting_start_time = 0.0

        # Recording
        self._recording_file = None

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block. Runs VAD frame-by-frame."""
        if status:
            log.warning("Audio status: %s", status)

        audio = indata.flatten().astype(np.float32)

        # Write full audio to recording file (unfiltered — archive everything)
        if self._recording_file:
            audio_int16 = (audio * 32767).astype(np.int16)
            self._recording_file.writeframes(audio_int16.tobytes())

        # Broadcast audio level for waveform display
        rms = float(np.sqrt(np.mean(audio ** 2)))
        peak = float(np.max(np.abs(audio)))
        if self._level_queue and self._loop:
            try:
                self._loop.call_soon_threadsafe(
                    self._level_queue.put_nowait, {"rms": rms, "peak": peak}
                )
            except asyncio.QueueFull:
                pass

        # Process audio through VAD in FRAME_SIZE (512 sample / 32ms) chunks
        for i in range(0, len(audio), FRAME_SIZE):
            frame = audio[i : i + FRAME_SIZE]
            if len(frame) < FRAME_SIZE:
                break  # skip incomplete final frame
            self._process_vad_frame(frame)

    def _process_vad_frame(self, frame: np.ndarray):
        """VAD state machine. Called for each 32ms frame."""
        prob = self._vad.process_frame(frame)
        is_speech = prob >= self._vad.threshold

        if self._state == CaptureState.IDLE:
            # Keep a rolling pre-buffer so we don't miss speech onset
            self._pre_buffer.append(frame)
            if len(self._pre_buffer) > self._pre_buffer_max:
                self._pre_buffer.pop(0)

            if is_speech:
                # Speech started — transition to SPEAKING
                self._state = CaptureState.SPEAKING
                self._speech_start_time = time.time()
                # Include pre-buffer so we capture the very start of speech
                self._speech_frames = list(self._pre_buffer)
                self._speech_frames.append(frame)
                self._pre_buffer = []
                self._trailing_silence_frames = 0
                log.debug("VAD: speech onset (prob=%.2f)", prob)

        elif self._state == CaptureState.SPEAKING:
            self._speech_frames.append(frame)

            if not is_speech:
                # Silence during speech — might be a pause or end of utterance
                self._state = CaptureState.TRAILING
                self._trailing_silence_frames = 1
            elif len(self._speech_frames) >= self._max_segment_frames:
                # Force-emit: someone has been talking for MAX_SEGMENT_DURATION_S
                log.info("VAD: force-emit after %.1fs continuous speech", MAX_SEGMENT_DURATION_S)
                self._emit_segment()

        elif self._state == CaptureState.TRAILING:
            self._speech_frames.append(frame)

            if is_speech:
                # Speech resumed — back to SPEAKING (was just a pause)
                self._state = CaptureState.SPEAKING
                self._trailing_silence_frames = 0
            else:
                self._trailing_silence_frames += 1
                if self._trailing_silence_frames >= self._trailing_silence_max:
                    # Confirmed end of utterance
                    self._emit_segment()

    def _emit_segment(self):
        """Package accumulated speech frames into a SpeechSegment and enqueue it."""
        if not self._speech_frames:
            self._state = CaptureState.IDLE
            return

        audio = np.concatenate(self._speech_frames)

        # Discard very short segments (clicks, pops, single coughs)
        if len(audio) < self._min_segment_samples:
            log.debug("VAD: discarding short segment (%.0fms)", len(audio) / self.sample_rate * 1000)
            self._state = CaptureState.IDLE
            self._speech_frames = []
            return

        elapsed = self._speech_start_time - self._meeting_start_time
        segment = SpeechSegment(audio=audio, start_time=elapsed)

        log.info(
            "VAD: speech segment — %.0fms, RMS=%.4f",
            segment.duration_ms, segment.rms,
        )

        if self._segment_queue and self._loop:
            try:
                self._loop.call_soon_threadsafe(self._segment_queue.put_nowait, segment)
            except asyncio.QueueFull:
                log.warning("Segment queue full — dropping segment")

        # Reset state
        self._state = CaptureState.IDLE
        self._speech_frames = []
        self._pre_buffer = []

    async def start(self, recording_path: Path | None = None, meeting_start_time: float | None = None) -> asyncio.Queue:
        """Start VAD-driven audio capture.

        Returns an async queue that yields SpeechSegment objects (not raw chunks).
        """
        import sounddevice as sd

        self._segment_queue = asyncio.Queue(maxsize=50)
        self._level_queue = asyncio.Queue(maxsize=20)
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._meeting_start_time = meeting_start_time or time.time()

        # Load VAD
        self._vad.load()

        # Open recording file (archives everything, not just speech)
        self._recording_file = None
        if recording_path:
            recording_path.parent.mkdir(parents=True, exist_ok=True)
            self._recording_file = wave.open(str(recording_path), "wb")
            self._recording_file.setnchannels(CHANNELS)
            self._recording_file.setsampwidth(2)
            self._recording_file.setframerate(self.sample_rate)
            log.info("Recording to %s", recording_path)

        # Check for input device before starting
        default_input = sd.default.device[0]
        if default_input < 0:
            # No microphone connected — list available devices for debugging
            devices = sd.query_devices()
            input_devs = [f"[{i}] {d['name']}" for i, d in enumerate(devices) if d["max_input_channels"] > 0]
            if input_devs:
                log.error("No default input device. Available inputs: %s", ", ".join(input_devs))
            else:
                log.error("No audio input device found. Connect a microphone.")
            # Signal immediate end so the pipeline doesn't hang
            self._segment_queue.put_nowait(None)
            return self._segment_queue

        # Start audio stream with small blocks for responsive VAD
        # 100ms blocks = 1600 samples at 16kHz → 3 VAD frames per block
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=int(self.sample_rate * 0.1),  # 100ms blocks
            callback=self._audio_callback,
        )
        self._stream.start()
        log.info("Audio capture started (VAD-driven, %dHz, device=%d)", self.sample_rate, default_input)
        return self._segment_queue

    async def stop(self):
        """Stop capture. Emit any pending speech segment."""
        self._running = False

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.warning("Error stopping audio stream: %s", e)
            self._stream = None

        # Emit any in-progress speech segment
        if self._speech_frames:
            self._emit_segment()

        # Close recording file
        if self._recording_file:
            try:
                self._recording_file.close()
            except Exception:
                pass
            self._recording_file = None
            log.info("Recording saved")

        # Signal end
        if self._segment_queue:
            try:
                self._segment_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    @property
    def level_queue(self) -> asyncio.Queue | None:
        return self._level_queue

    @property
    def state(self) -> CaptureState:
        return self._state
