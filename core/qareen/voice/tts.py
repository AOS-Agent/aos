"""ElevenLabs Streaming TTS for Qareen voice output.

Connects to ElevenLabs WebSocket API, sends text, yields MP3 audio chunks.
First audio chunk arrives in ~200-300ms. Falls back to HTTP streaming if
the ``websockets`` package is not installed.

Usage:
    tts = TTSService()
    await tts.initialize()

    async for chunk in tts.speak("Hello from Qareen"):
        # chunk is bytes (MP3 encoded)
        await websocket.send_bytes(chunk)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from qareen.events.bus import EventBus

from qareen.events.types import Event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default voice configuration
# ---------------------------------------------------------------------------

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"
OUTPUT_FORMAT = "mp3_44100_64"

# Agent-secret script path (used to pull API key from macOS Keychain)
_AGENT_SECRET_PATH = os.path.expanduser("~/aos/core/bin/cli/agent-secret")


class TTSService:
    """Streaming text-to-speech via ElevenLabs.

    Supports two transport modes:

    1. **WebSocket** (preferred) — lowest latency (~200-300ms to first audio).
       Requires the ``websockets`` package.
    2. **HTTP streaming** (fallback) — works with stdlib only, slightly
       higher latency.

    The service pulls its API key from macOS Keychain via ``agent-secret``
    at initialization. If the key is not available, TTS is gracefully
    disabled and ``available`` returns ``False``.
    """

    def __init__(
        self,
        voice_id: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
    ):
        self._voice_id = voice_id or DEFAULT_VOICE_ID
        self._model_id = model_id
        self._api_key: str | None = None
        self._available = False
        self._speaking = False
        self._cancel = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        """Load API key from keychain. Returns True if TTS is available.

        Tries, in order:
        1. macOS Keychain via ``agent-secret get elevenlabs-api-key``
        2. Environment variable ``ELEVENLABS_API_KEY``

        If neither source yields a valid key, TTS is marked unavailable.
        """
        # Try agent-secret first
        key = await self._load_key_from_keychain()

        # Fallback to environment variable
        if not key:
            key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

        if key and len(key) > 10:
            self._api_key = key
            self._available = True
            logger.info("TTS service initialized (ElevenLabs, voice=%s)", self._voice_id)
            return True

        logger.info("TTS service unavailable (no ElevenLabs API key)")
        return False

    async def _load_key_from_keychain(self) -> str:
        """Attempt to read the API key from macOS Keychain."""
        try:
            if not os.path.exists(_AGENT_SECRET_PATH):
                logger.debug("agent-secret not found at %s", _AGENT_SECRET_PATH)
                return ""

            proc = await asyncio.create_subprocess_exec(
                "python3", _AGENT_SECRET_PATH,
                "get", "elevenlabs-api-key",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode != 0:
                logger.debug("agent-secret returned %d: %s", proc.returncode, stderr.decode().strip())
                return ""

            return stdout.decode().strip()

        except asyncio.TimeoutError:
            logger.warning("agent-secret timed out after 5s")
            return ""
        except Exception as e:
            logger.debug("Failed to load API key from keychain: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether TTS is initialized and ready to speak."""
        return self._available

    @property
    def speaking(self) -> bool:
        """Whether audio is currently being generated."""
        return self._speaking

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def cancel(self):
        """Cancel current speech generation (barge-in support)."""
        self._cancel = True

    def set_voice(self, voice_id: str):
        """Switch to a different ElevenLabs voice.

        Takes effect on the next ``speak()`` call.
        """
        self._voice_id = voice_id
        logger.info("TTS voice changed to %s", voice_id)

    # ------------------------------------------------------------------
    # Core speak method
    # ------------------------------------------------------------------

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """Stream MP3 audio chunks for the given text.

        Yields bytes (MP3 encoded audio chunks) as they arrive from
        ElevenLabs. First chunk typically arrives in ~200-300ms.

        Prefers WebSocket transport for lowest latency. Falls back to
        HTTP streaming if ``websockets`` is not installed.
        """
        if not self._available or not text.strip():
            return

        self._speaking = True
        self._cancel = False

        try:
            # Prefer WebSocket transport (lowest latency)
            try:
                import websockets  # noqa: F401
                async for chunk in self._speak_ws(text):
                    yield chunk
            except ImportError:
                logger.debug("websockets not installed, using HTTP streaming fallback")
                async for chunk in self._speak_http(text):
                    yield chunk
        finally:
            self._speaking = False
            self._cancel = False

    # ------------------------------------------------------------------
    # WebSocket transport (preferred)
    # ------------------------------------------------------------------

    async def _speak_ws(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio via ElevenLabs WebSocket API — lowest latency."""
        import websockets

        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/"
            f"{self._voice_id}/stream-input"
            f"?model_id={self._model_id}"
            f"&output_format={OUTPUT_FORMAT}"
        )

        try:
            async with websockets.connect(
                url,
                additional_headers={"xi-api-key": self._api_key},
            ) as ws:
                # Send initial configuration (voice settings + chunking config)
                await ws.send(json.dumps({
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                    "generation_config": {
                        "chunk_length_schedule": [120, 160, 250, 290],
                    },
                }))

                # Send the full text
                await ws.send(json.dumps({"text": text}))

                # Signal end of input
                await ws.send(json.dumps({"text": ""}))

                # Yield audio chunks as they arrive
                async for message in ws:
                    if self._cancel:
                        logger.debug("TTS cancelled mid-stream (barge-in)")
                        break

                    data = json.loads(message)
                    if data.get("audio"):
                        yield base64.b64decode(data["audio"])
                    if data.get("isFinal"):
                        break

        except Exception as e:
            logger.error("ElevenLabs WebSocket error: %s", e)

    # ------------------------------------------------------------------
    # HTTP streaming transport (fallback)
    # ------------------------------------------------------------------

    async def _speak_http(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio via ElevenLabs HTTP API — no external deps required."""
        import urllib.request

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/"
            f"{self._voice_id}/stream"
            f"?output_format={OUTPUT_FORMAT}"
        )

        req = urllib.request.Request(
            url,
            data=json.dumps({
                "text": text,
                "model_id": self._model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            }).encode(),
            headers={
                "xi-api-key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )

        loop = asyncio.get_event_loop()

        try:
            response = await loop.run_in_executor(None, urllib.request.urlopen, req)

            while True:
                if self._cancel:
                    logger.debug("TTS cancelled mid-stream (barge-in)")
                    break

                chunk = await loop.run_in_executor(None, response.read, 4096)
                if not chunk:
                    break
                yield chunk

        except Exception as e:
            logger.error("ElevenLabs HTTP streaming error: %s", e)


# ---------------------------------------------------------------------------
# WebSocket handler for browser clients
# ---------------------------------------------------------------------------

async def handle_tts_websocket(
    websocket,
    tts_service: TTSService,
    bus: EventBus | None = None,
):
    """Handle a TTS WebSocket connection from the browser.

    Protocol:
        Browser sends:  {"type": "speak", "text": "Hello world"}
        Backend sends:  binary MP3 chunks, then {"type": "done"}
        Browser sends:  {"type": "stop"} for barge-in

    The handler loops indefinitely, processing speak/stop commands
    until the client disconnects.

    Args:
        websocket: A FastAPI WebSocket (already accepted).
        tts_service: An initialized TTSService instance.
        bus: Optional EventBus for emitting tts.speaking / tts.done events.
    """
    try:
        while True:
            msg = await websocket.receive_json()

            if msg.get("type") == "speak":
                text = msg.get("text", "").strip()
                if not text:
                    await websocket.send_json({"type": "done"})
                    continue

                if not tts_service.available:
                    await websocket.send_json({
                        "type": "error",
                        "message": "TTS service not available",
                    })
                    continue

                # Emit speaking event
                if bus:
                    await bus.emit(Event(
                        event_type="tts.speaking",
                        timestamp=datetime.now(),
                        payload={"text": text[:200]},
                        source="tts",
                    ))

                logger.info("TTS speaking: %s", text[:80])

                # Stream audio chunks to the browser
                async for chunk in tts_service.speak(text):
                    await websocket.send_bytes(chunk)

                await websocket.send_json({"type": "done"})

                # Emit done event
                if bus:
                    await bus.emit(Event(
                        event_type="tts.done",
                        timestamp=datetime.now(),
                        payload={},
                        source="tts",
                    ))

            elif msg.get("type") == "stop":
                tts_service.cancel()
                await websocket.send_json({"type": "stopped"})
                logger.debug("TTS stopped (barge-in)")

    except Exception as e:
        # WebSocketDisconnect and similar are expected — only log unexpected errors
        err_msg = str(e).lower()
        if "disconnect" not in err_msg and "close" not in err_msg:
            logger.warning("TTS WebSocket error: %s", e)
        else:
            logger.debug("TTS WebSocket disconnected")
