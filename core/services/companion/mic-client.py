#!/usr/bin/env python3
"""AOS Mic Client — stream speech from MacBook to Mac Mini over Tailscale.

Captures audio from local mic, runs Silero VAD locally (so only speech
is sent over the network), and streams raw PCM segments via WebSocket
to the companion service on the Mac Mini.

Usage:
    python3 mic-client.py                          # auto-discover Mac Mini
    python3 mic-client.py --host 100.112.113.53    # explicit host
    python3 mic-client.py --device 1               # specific mic device

Requirements (install on MacBook):
    pip install sounddevice numpy onnxruntime websockets
"""

import argparse
import asyncio
import logging
import struct

import numpy as np
import sounddevice as sd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mic-client")

# -- Constants --
SAMPLE_RATE = 16000
FRAME_SIZE = 512  # 32ms at 16kHz — Silero VAD frame size
VAD_THRESHOLD = 0.5
PRE_SPEECH_BUFFER_MS = 300
TRAILING_SILENCE_MS = 600
MAX_SEGMENT_S = 15
MIN_SEGMENT_MS = 300
INTERIM_INTERVAL_S = 1.0  # Send interim audio every 1s during speech for live partial transcription
DEFAULT_PORT = 7603
MODEL_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"


# -- Energy-based VAD (simple RMS threshold — works on any mic) --

class VAD:
    def __init__(self, threshold=VAD_THRESHOLD):
        self.threshold = threshold  # RMS threshold for speech
        self._noise_floor = 0.005  # Will be calibrated

    def load(self):
        log.info("VAD loaded (energy-based, threshold=%.3f)", self.threshold)

    def calibrate(self, noise_rms: float):
        """Set noise floor from calibration recording."""
        self._noise_floor = noise_rms
        # Speech threshold = 3x noise floor, minimum 0.008
        self.threshold = max(noise_rms * 3.0, 0.008)
        log.info("VAD calibrated: noise=%.4f, threshold=%.4f", noise_rms, self.threshold)

    def process(self, frame: np.ndarray) -> float:
        """Returns RMS energy of the frame (used as speech probability)."""
        return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

    def reset(self):
        pass


# -- Audio capture with VAD --

class MicCapture:
    def __init__(self, device=None, gain: float = 1.0):
        self.device = device
        self.gain = gain  # Amplify mic signal before VAD (some mics are very quiet)
        self.vad = VAD()
        self._queue = None
        self._loop = None
        self._stream = None

        # State machine
        self._state = "idle"  # idle, speaking, trailing
        self._speech_frames = []
        self._pre_buffer = []
        self._pre_buffer_max = int(PRE_SPEECH_BUFFER_MS / 1000 * SAMPLE_RATE / FRAME_SIZE)
        self._trailing_count = 0
        self._trailing_max = int(TRAILING_SILENCE_MS / 1000 * SAMPLE_RATE / FRAME_SIZE)
        self._max_frames = int(MAX_SEGMENT_S * SAMPLE_RATE / FRAME_SIZE)
        self._min_samples = int(MIN_SEGMENT_MS / 1000 * SAMPLE_RATE)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Audio: %s", status)
        audio = indata.flatten().astype(np.float32)
        # Apply gain — some mics (MacBook built-in) are very quiet
        if self.gain != 1.0:
            audio = np.clip(audio * self.gain, -1.0, 1.0)
        for i in range(0, len(audio), FRAME_SIZE):
            frame = audio[i : i + FRAME_SIZE]
            if len(frame) < FRAME_SIZE:
                break
            self._process_frame(frame)

    def _process_frame(self, frame):
        rms = self.vad.process(frame)
        is_speech = rms >= self.vad.threshold

        if self._state == "idle":
            self._pre_buffer.append(frame)
            if len(self._pre_buffer) > self._pre_buffer_max:
                self._pre_buffer.pop(0)
            if is_speech:
                self._state = "speaking"
                self._speech_frames = list(self._pre_buffer)
                self._speech_frames.append(frame)
                self._pre_buffer = []
                self._trailing_count = 0
                log.debug("Speech onset")

        elif self._state == "speaking":
            self._speech_frames.append(frame)
            if not is_speech:
                self._state = "trailing"
                self._trailing_count = 1
            elif len(self._speech_frames) >= self._max_frames:
                self._emit()

        elif self._state == "trailing":
            self._speech_frames.append(frame)
            if is_speech:
                self._state = "speaking"
                self._trailing_count = 0
            else:
                self._trailing_count += 1
                if self._trailing_count >= self._trailing_max:
                    self._emit()

    def _emit(self):
        if not self._speech_frames:
            self._state = "idle"
            return
        audio = np.concatenate(self._speech_frames)
        if len(audio) < self._min_samples:
            self._state = "idle"
            self._speech_frames = []
            return

        duration_ms = len(audio) / SAMPLE_RATE * 1000
        rms = float(np.sqrt(np.mean(audio ** 2)))
        log.info("Speech: %.0fms, RMS=%.3f", duration_ms, rms)

        if self._queue and self._loop:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, audio)
            except asyncio.QueueFull:
                log.warning("Queue full, dropping segment")

        self._state = "idle"
        self._speech_frames = []
        self._pre_buffer = []

    async def start(self):
        self.vad.load()
        self._queue = asyncio.Queue(maxsize=20)
        self._loop = asyncio.get_running_loop()

        # Show device info
        if self.device is not None:
            info = sd.query_devices(self.device)
        else:
            info = sd.query_devices(sd.default.device[0])
        log.info("Mic: %s", info["name"])

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * 0.1),
            callback=self._callback,
            device=self.device,
        )
        self._stream.start()
        log.info("Listening... (speak to send)")
        return self._queue

    async def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
        if self._speech_frames:
            self._emit()


# -- WebSocket client --

async def calibrate_gain(device=None) -> float:
    """Record 2 seconds and determine if gain boost is needed."""
    log.info("Calibrating mic level... speak normally for 2 seconds.")
    audio = sd.rec(int(2 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    sd.wait()
    rms = float(np.sqrt(np.mean(audio ** 2)))
    log.info("Mic RMS: %.4f", rms)

    if rms < 0.001:
        log.warning("Mic appears silent — check permissions or mic selection.")
    else:
        log.info("Mic baseline: RMS=%.4f (this is your silence/ambient level)", rms)
    return rms  # Return noise floor for VAD calibration


async def stream_to_server(host: str, port: int, device=None, gain: float = 0.0):
    import websockets

    # Calibrate: record 2s of silence to measure noise floor
    noise_floor = await calibrate_gain(device)

    mic = MicCapture(device=device, gain=1.0)  # No gain needed with energy VAD
    mic.vad.calibrate(noise_floor)  # Set threshold based on noise floor
    queue = await mic.start()

    url = f"ws://{host}:{port}/ws/mic"
    log.info("Connecting to %s ...", url)

    try:
        async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
            log.info("Connected! Speak into the mic — speech streams to Mac Mini.")
            log.info("Press Ctrl+C to stop.")

            while True:
                audio = await queue.get()
                if audio is None:
                    break

                # Send as raw PCM float32 bytes with a small header
                # Header: 4 bytes sample_rate (uint32) + 4 bytes num_samples (uint32)
                header = struct.pack("<II", SAMPLE_RATE, len(audio))
                payload = header + audio.astype(np.float32).tobytes()
                await ws.send(payload)

                duration_ms = len(audio) / SAMPLE_RATE * 1000
                log.info("Sent %.0fms segment (%d bytes)", duration_ms, len(payload))

    except websockets.exceptions.ConnectionClosed:
        log.warning("Connection closed by server")
    except ConnectionRefusedError:
        log.error("Cannot connect to %s:%d — is the companion service running?", host, port)
    except Exception as e:
        log.error("Connection error: %s", e)
    finally:
        await mic.stop()


# -- Main --

def list_devices():
    print("\nAudio input devices:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            default = " (DEFAULT)" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']} ({d['max_input_channels']}ch, {int(d['default_samplerate'])}Hz){default}")
    print()


def main():
    parser = argparse.ArgumentParser(description="AOS Mic Client — stream speech to Mac Mini")
    parser.add_argument("--host", default="100.112.113.53", help="Mac Mini IP (default: Tailscale)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Companion port (default: 7603)")
    parser.add_argument("--device", type=int, default=None, help="Audio input device index")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--threshold", type=float, default=VAD_THRESHOLD, help="VAD threshold (default: 0.5)")
    parser.add_argument("--gain", type=float, default=0, help="Mic gain multiplier (0=auto-calibrate, default: auto)")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    # Check for default input device
    if args.device is None and sd.default.device[0] < 0:
        log.error("No default input device. Use --list-devices to see options, --device N to select.")
        list_devices()
        return

    try:
        asyncio.run(stream_to_server(args.host, args.port, device=args.device, gain=args.gain))
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
