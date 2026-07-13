"""Action Detector Pipeline — surface actionable cards from thought-units.

Subscribes to stream.unit events. When a unit's classification indicates
an actionable intent (task, decision, idea) and its confidence exceeds
the learned threshold, emits a card event for the UI to display.

Emits: card
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import uuid4

from ...events.types import Event
from .base import Pipeline

logger = logging.getLogger(__name__)

# Classification -> card_type mapping
_CARD_TYPE_MAP = {
    "task": "task",
    "decision": "decision",
    "idea": "vault",
}


class ActionDetectorPipeline(Pipeline):
    """Detect actionable units and emit card events."""

    def wire(self) -> None:
        self._bus.subscribe("stream.unit", self._on_unit)

    async def _on_unit(self, event: Event) -> None:
        try:
            payload = event.payload
            classification = payload.get("classification", "")
            card_type = _CARD_TYPE_MAP.get(classification)
            if card_type is None:
                return

            confidence = payload.get("confidence", 0.0)
            if not isinstance(confidence, (int, float)):
                return

            # Check learned threshold
            threshold = 0.70
            if self._context is not None:
                try:
                    threshold = self._context.get_threshold(classification)
                except Exception:
                    logger.debug(
                        "ActionDetector: threshold lookup failed for %r",
                        classification,
                    )

            if confidence < threshold:
                return

            text = payload.get("text", "")
            thread_id = payload.get("thread_id", "")
            card_id = uuid4().hex[:12]
            now = datetime.now().isoformat()

            title = text[:80].strip()
            if len(text) > 80:
                title = title.rsplit(" ", 1)[0] + "..."

            await self._bus.emit(Event(
                event_type="card",
                timestamp=datetime.now(),
                source="action_detector",
                payload={
                    "id": card_id,
                    "card_type": card_type,
                    "title": title,
                    "body": text,
                    "confidence": round(float(confidence), 3),
                    "thread_id": thread_id,
                    "created_at": now,
                },
            ))

            logger.info(
                "ActionDetector: emitted %s card %s (confidence=%.2f)",
                card_type,
                card_id,
                confidence,
            )
        except Exception:
            logger.exception("ActionDetector: failed processing stream.unit")
