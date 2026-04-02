"""Voice Activity Detection — Silero VAD v5 via ONNX runtime.

Processes 512-sample frames (32ms at 16kHz) and returns speech probability.
Runs on CPU, ~1ms per frame, ~2MB model. No GPU contention.
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("companion.vad")

MODEL_PATH = Path.home() / ".aos" / "models" / "silero_vad.onnx"
SAMPLE_RATE = 16000
FRAME_SIZE = 512  # 32ms at 16kHz — Silero VAD's expected frame size


class VoiceActivityDetector:
    """Silero VAD v5 wrapper. Stateful — maintains LSTM hidden state across frames."""

    def __init__(self, threshold: float = 0.5, sample_rate: int = SAMPLE_RATE):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._session = None
        self._state = None  # Silero VAD v5: single state tensor (2, 1, 128)

    def load(self):
        """Load the ONNX model. Call once at startup."""
        import onnxruntime as ort

        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Silero VAD model not found at {MODEL_PATH}. "
                "Download from https://github.com/snakers4/silero-vad"
            )

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(str(MODEL_PATH), sess_options=opts)
        self.reset()
        log.info("Silero VAD loaded (%.1f MB)", MODEL_PATH.stat().st_size / 1e6)

    def reset(self):
        """Reset LSTM state. Call between unrelated audio streams."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def process_frame(self, frame: np.ndarray) -> float:
        """Process a single 512-sample frame. Returns speech probability [0.0, 1.0]."""
        if self._session is None:
            raise RuntimeError("VAD not loaded — call load() first")

        # Ensure correct shape: (1, 512)
        audio = frame.flatten().astype(np.float32)
        if len(audio) != FRAME_SIZE:
            raise ValueError(f"Expected {FRAME_SIZE} samples, got {len(audio)}")

        ort_inputs = {
            "input": audio.reshape(1, -1),
            "state": self._state,
            "sr": np.array(self.sample_rate, dtype=np.int64),
        }

        output, self._state = self._session.run(None, ort_inputs)
        return float(output[0][0])

    def is_speech(self, frame: np.ndarray) -> bool:
        """Convenience: process frame and return True if speech detected."""
        return self.process_frame(frame) >= self.threshold
