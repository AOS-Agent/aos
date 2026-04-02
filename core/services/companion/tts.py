"""Text-to-Speech — Kokoro 82M via mlx-audio on Apple Silicon.

Loads the model once at startup (~600MB), generates 24kHz audio.
Supports streaming playback with interrupt (stop mid-sentence).
~300-500ms for short phrases on M4.
"""

import asyncio
import logging
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("companion.tts")

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "bf_emma"  # warm British female
TTS_SAMPLE_RATE = 24000


class TextToSpeech:
    """Kokoro TTS engine. Generates speech and plays via sounddevice."""

    def __init__(self, model_id: str = DEFAULT_MODEL, voice: str = DEFAULT_VOICE):
        self._model_id = model_id
        self._model = None
        self.voice = voice
        self._stream: sd.OutputStream | None = None
        self._interrupt = threading.Event()
        self._speaking = threading.Event()
        self._playback_lock = threading.Lock()

    def load(self):
        """Load Kokoro model into GPU memory. Call once at startup."""
        import os

        from mlx_audio.tts.utils import load_model

        # Ensure VIRTUAL_ENV is set so misaki/spacy can find packages
        if "VIRTUAL_ENV" not in os.environ:
            venv = os.path.dirname(os.path.dirname(os.path.abspath(__import__("sys").executable)))
            os.environ["VIRTUAL_ENV"] = venv

        t0 = time.time()
        self._model = load_model(self._model_id)
        log.info("TTS model loaded: %s (%.1fs)", self._model_id, time.time() - t0)

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def interrupt(self):
        """Stop current speech immediately. Safe to call from any thread."""
        self._interrupt.set()

    def speak_sync(self, text: str, voice: str | None = None, device: int | None = None,
                   speed: float = 1.0) -> bool:
        """Generate and play speech synchronously. Returns True if completed, False if interrupted."""
        if self._model is None:
            self.load()

        voice = voice or self.voice
        self._interrupt.clear()
        self._speaking.set()

        t0 = time.time()
        completed = False

        try:
            with self._playback_lock:
                stream = sd.OutputStream(
                    samplerate=TTS_SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    device=device,
                    blocksize=1024,
                    latency="low",
                )
                stream.start()
                self._stream = stream

                # Determine language code from voice prefix: b=British, a=American
                lang_code = "b" if voice.startswith("b") else "a"

                first_chunk = True
                for result in self._model.generate(
                    text=text, voice=voice, speed=speed, lang_code=lang_code
                ):
                    if self._interrupt.is_set():
                        log.info("TTS interrupted after %.0fms", (time.time() - t0) * 1000)
                        break

                    audio = result.audio
                    if isinstance(audio, list):
                        audio = np.array(audio[0], dtype=np.float32)
                    elif not isinstance(audio, np.ndarray):
                        audio = np.array(audio, dtype=np.float32)

                    audio = audio.flatten().astype(np.float32)

                    if first_chunk:
                        log.info(
                            "TTS first chunk: %.0fms — %s",
                            (time.time() - t0) * 1000, text[:60],
                        )
                        first_chunk = False

                    # Write in small blocks to allow interrupt checks
                    block_size = TTS_SAMPLE_RATE // 4  # 250ms blocks
                    for i in range(0, len(audio), block_size):
                        if self._interrupt.is_set():
                            break
                        block = audio[i : i + block_size]
                        stream.write(block.reshape(-1, 1))

                else:
                    completed = True

                stream.stop()
                stream.close()
                self._stream = None

        finally:
            self._speaking.clear()
            self._interrupt.clear()

        if completed:
            log.info("TTS complete: %.0fms total", (time.time() - t0) * 1000)
        return completed

    async def speak(self, text: str, voice: str | None = None, device: int | None = None,
                    speed: float = 1.0) -> bool:
        """Async wrapper — runs TTS in a thread to avoid blocking the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.speak_sync(text, voice=voice, device=device, speed=speed)
        )

    def list_voices(self) -> list[str]:
        """Return available Kokoro voice IDs."""
        return [
            "af_heart", "af_bella", "af_nova", "af_sarah", "af_sky",
            "am_adam", "am_michael",
            "bf_emma", "bf_isabella",
            "bm_george", "bm_lewis",
        ]

    @staticmethod
    def find_device(name_substring: str) -> int | None:
        """Find an audio output device by name (e.g., 'AirPods'). Returns device index or None."""
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if name_substring.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
                return i
        return None
