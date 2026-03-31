"""WebSocket endpoint for browser audio streaming."""

from __future__ import annotations

import logging
import struct

import numpy as np
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

HEADER_SIZE = 8


async def audio_stream(websocket: WebSocket):
    """Receive audio from browser, process through voice pipeline."""
    await websocket.accept()
    logger.info("Audio WebSocket connected")

    app = websocket.app
    voice_manager = getattr(app.state, "voice_manager", None)

    if not voice_manager:
        logger.warning("No voice manager — closing")
        await websocket.close(code=1011, reason="No voice manager")
        return

    chunks = 0
    try:
        async for raw in websocket.iter_bytes():
            chunks += 1
            if chunks <= 3 or chunks % 50 == 0:
                logger.info("Audio chunk #%d: %d bytes", chunks, len(raw))

            if len(raw) < HEADER_SIZE:
                continue

            sample_rate, num_samples = struct.unpack_from("<II", raw, 0)
            expected = HEADER_SIZE + num_samples * 4

            if len(raw) < expected:
                continue

            audio = np.frombuffer(
                raw[HEADER_SIZE : HEADER_SIZE + num_samples * 4],
                dtype=np.float32,
            )
            await voice_manager.process_chunk(audio)

    except WebSocketDisconnect:
        logger.info("Audio WebSocket disconnected after %d chunks", chunks)
    except Exception as e:
        logger.error("Audio WebSocket error after %d chunks: %s", chunks, e)
    finally:
        if voice_manager:
            await voice_manager.on_disconnect()
        logger.info("Audio WebSocket cleanup complete")


def register(app):
    """Register the WebSocket route on the app."""
    app.add_websocket_route("/ws/audio", audio_stream)
