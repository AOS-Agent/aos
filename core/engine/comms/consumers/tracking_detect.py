"""CommsBus front-end for tracking detection.

The detection logic lives in ``core.engine.bus.consumers.tracking_detect``
(``TrackingDetectConsumer.detect_message``), which was written against the
eventd EventBus — a *different* bus with a different consumer contract:

    EventBus   consumer.process(event: Event)      -> None
    CommsBus   consumer.process(messages: list[Message]) -> int

Because the two contracts collide on the same method name, registering the
EventBus consumer directly on the live CommsBus does not fail loudly — it
gets handed a *list* where it expects an Event, raises inside its own
never-raise guard, and silently detects nothing forever. That is precisely
how Auto Tracker shipped: the detection pipeline was complete, tested, and
never connected to the bus that actually runs.

This adapter is the missing seam. It translates the unified Message into the
normalized dict the detector expects and delegates to the shared
``detect_message`` path, so the two front-ends cannot drift.

Posture: MUST NEVER raise. Detection is best-effort enrichment; a bad pack
or an unavailable store can never take down comms ingestion.
"""

from __future__ import annotations

import logging

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)


class TrackingDetectConsumer(Consumer):
    """Detect shipment tracking numbers in inbound CommsBus messages."""

    name = "tracking_detect"

    def __init__(self, detector=None):
        # Injectable for tests; None → lazy-load the real detector.
        self._detector = detector

    def _get_detector(self):
        if self._detector is None:
            # Relative import: resolves correctly whether this module is
            # imported as core.comms.consumers.* (via the core/comms →
            # engine/comms symlink, the live bus path) or as
            # core.engine.comms.consumers.* — the repo has both.
            from ...bus.consumers.tracking_detect import (
                TrackingDetectConsumer as _Detector,
            )

            self._detector = _Detector()
        return self._detector

    @staticmethod
    def _to_message_dict(msg: Message) -> dict:
        """Unified Message → the dict shape qareen.tracking.detect expects.

        Note ``subject``: Message has no subject field — email adapters put
        it in metadata. Detection weights subject lines heavily for order
        confirmations, so dropping it would blind the primary channel.
        """
        meta = msg.metadata or {}
        return {
            "message_id": msg.id,
            "sender": msg.sender or "",
            "channel": msg.channel or "",
            "text": msg.text or "",
            "subject": meta.get("subject", "") or meta.get("Subject", "") or "",
            "conversation_id": msg.conversation_id or "",
            "timestamp": msg.timestamp,
            "from_me": False,
        }

    def process(self, messages: list[Message]) -> int:
        """Detect over a batch. Returns the number of messages inspected."""
        detector = None
        inspected = 0
        for msg in messages:
            try:
                if getattr(msg, "from_me", False) or msg.sender == "me":
                    continue
                if not (msg.text or (msg.metadata or {}).get("subject")):
                    continue
                if detector is None:
                    detector = self._get_detector()
                detector.detect_message(self._to_message_dict(msg))
                inspected += 1
            except Exception as exc:
                # Per-message isolation: one malformed message must not
                # abort detection for the rest of the batch.
                log.warning(
                    "tracking_detect: message %s failed (%s)",
                    getattr(msg, "id", "?"), exc,
                )
        return inspected
