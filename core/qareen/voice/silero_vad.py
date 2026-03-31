"""Silero VAD v5 wrapper for the voice pipeline.

Thin wrapper around the ONNX model at ~/.aos/models/silero_vad.onnx.
Processes 512-sample frames (32ms at 16kHz) and returns speech probability.

This is separate from the old companion service's vad.py to avoid import
entanglement. Same model, same interface, clean integration with VoiceManager.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

FRAME_SIZE = 512  # 32ms at 16kHz
SAMPLE_RATE = 16000


class SileroVAD:
    """Silero VAD v5 via ONNX. Stateful -- maintains LSTM hidden state."""

    def __init__(self, model_path: str, threshold: float = 0.5):
        self.threshold = threshold
        self._session = None
        self._state: np.ndarray | None = None

        self._load(model_path)

    def _load(self, model_path: str):
        """Load the ONNX model."""
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(model_path, sess_options=opts)
        self.reset()

    def reset(self):
        """Reset LSTM state. Call between utterances."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def process_frame(self, frame: np.ndarray) -> float:
        """Process a 512-sample frame. Returns speech probability [0.0, 1.0]."""
        if self._session is None:
            raise RuntimeError("VAD session not initialized")

        audio = frame.flatten().astype(np.float32)
        if len(audio) != FRAME_SIZE:
            raise ValueError(f"Expected {FRAME_SIZE} samples, got {len(audio)}")

        ort_inputs = {
            "input": audio.reshape(1, -1),
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        }

        output, self._state = self._session.run(None, ort_inputs)
        return float(output[0][0])

    def is_speech(self, frame: np.ndarray) -> bool:
        """Convenience: returns True if frame contains speech."""
        return self.process_frame(frame) >= self.threshold
