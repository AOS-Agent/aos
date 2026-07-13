"""Voice Responder Pipeline — manages when the qareen speaks.

Subscribes to stream.unit, research.result, and voice_state events.
Queues brief spoken responses and emits tts.speak events at appropriate
moments — only when the user is not speaking, and no more than once
every 10 seconds.

Emits: tts.speak
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from ...events.types import Event
from .base import Pipeline

logger = logging.getLogger(__name__)

# Minimum seconds between spoken responses
_SPEAK_COOLDOWN_S = 10

# Maximum words in a spoken response
_MAX_RESPONSE_WORDS = 15


class VoiceResponderPipeline(Pipeline):
    """Queue and deliver brief spoken responses at natural pauses."""

    def __init__(self, bus, context_store=None):
        super().__init__(bus, context_store)
        self._pending_responses: list[str] = []
        self._user_speaking: bool = False
        self._last_speak_time: float = 0.0

    def wire(self) -> None:
        self._bus.subscribe("stream.unit", self._on_unit)
        self._bus.subscribe("research.result", self._on_research)
        self._bus.subscribe("voice_state", self._on_voice_state)

    async def _on_unit(self, event: Event) -> None:
        try:
            payload = event.payload
            classification = payload.get("classification", "")

            if classification == "question":
                text = payload.get("text", "")
                brief = self._truncate(f"Looking into that for you.")
                self._enqueue(brief)
        except Exception:
            logger.exception("VoiceResponder: failed on stream.unit")

    async def _on_research(self, event: Event) -> None:
        try:
            payload = event.payload
            results = payload.get("vault_results", [])

            if results:
                count = len(results)
                brief = self._truncate(
                    f"Found {count} relevant note{'s' if count != 1 else ''} "
                    f"in the vault."
                )
                self._enqueue(brief)
        except Exception:
            logger.exception("VoiceResponder: failed on research.result")

    async def _on_voice_state(self, event: Event) -> None:
        try:
            payload = event.payload
            state = payload.get("state", "")

            was_speaking = self._user_speaking
            self._user_speaking = state == "listening"

            # User just stopped speaking — flush one pending response
            if was_speaking and not self._user_speaking:
                await self._try_speak()
        except Exception:
            logger.exception("VoiceResponder: failed on voice_state")

    def _enqueue(self, text: str) -> None:
        """Add a response to the pending queue (max 3 queued)."""
        if not text:
            return
        if len(self._pending_responses) >= 3:
            self._pending_responses.pop(0)
        self._pending_responses.append(text)

    async def _try_speak(self) -> None:
        """Emit tts.speak for the first queued response if cooldown allows."""
        if not self._pending_responses:
            return

        now = time.monotonic()
        if now - self._last_speak_time < _SPEAK_COOLDOWN_S:
            return

        text = self._pending_responses.pop(0)
        self._last_speak_time = now

        await self._bus.emit(Event(
            event_type="tts.speak",
            timestamp=datetime.now(),
            source="voice_responder",
            payload={"text": text},
        ))

        logger.info("VoiceResponder: spoke %r", text)

    @staticmethod
    def _truncate(text: str) -> str:
        """Trim response to at most _MAX_RESPONSE_WORDS words."""
        words = text.split()
        if len(words) <= _MAX_RESPONSE_WORDS:
            return text
        return " ".join(words[:_MAX_RESPONSE_WORDS]) + "..."
