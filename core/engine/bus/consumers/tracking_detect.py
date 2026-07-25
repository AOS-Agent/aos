"""Tracking-detect consumer — auto-detect shipments from inbound messages.

Subscribes to comms.message_received and runs the tracking detection
pipeline (qareen.tracking.detect) over each inbound message: URL extraction,
carrier digests, body pattern scan, context scoring, with probe/LLM layers
log-only by default (config-gated).

Posture: this consumer MUST NEVER raise. Every failure is logged and routed
to on_error — a broken pack, a missing store, or a garbage message can
never take down the event bus. The store is a duck-typed seam
(qareen.tracking.store, built concurrently); when it's unavailable the
consumer still detects and logs what it WOULD have persisted.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..consumer import EventConsumer
from ..event import Event

# Make the `qareen` package importable: consumers/ → parents[2] is core/.
_CORE = Path(__file__).resolve().parents[2]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

log = logging.getLogger(__name__)


class TrackingDetectConsumer(EventConsumer):
    """Detect tracking numbers in inbound comms messages.

    Confidence → action (initiative §2): >= 0.85 auto-add, 0.5–0.85
    approval queue, < 0.5 ignore — banding and thresholds live in
    qareen.tracking.config.
    """

    name = "tracking_detect"
    handles = ["comms.message_received"]

    def __init__(self, packs=None, store=None, config=None):
        # Injectable for tests; None → lazy-load the real thing on first use.
        self._packs = packs
        self._store = store
        self._config = config
        self._store_failed = False

    # ── lazy dependencies ─────────────────────────────────────────────

    def _get_config(self):
        if self._config is None:
            from qareen.tracking.config import TrackingConfig

            self._config = TrackingConfig.load()
        return self._config

    def _get_packs(self):
        if self._packs is None:
            from qareen.tracking.packs import load_packs

            self._packs = load_packs()
        return self._packs

    def _get_store(self):
        """Best-effort real store; None (log-only detection) on any failure."""
        if self._store is None and not self._store_failed:
            try:
                from qareen.tracking import store as tracking_store

                for opener in ("open_default", "open", "connect"):
                    fn = getattr(tracking_store, opener, None)
                    if callable(fn):
                        self._store = fn()
                        break
                else:
                    self._store = tracking_store
            except Exception as exc:
                self._store_failed = True
                log.warning("tracking_detect: store unavailable (%s) — log-only", exc)
        return self._store

    # ── event handling ────────────────────────────────────────────────

    def process(self, event: Event) -> None:
        """Handle one comms.message_received event. Never raises."""
        try:
            self._process(event)
        except Exception as exc:
            try:
                self.on_error(exc, event)
            except Exception:  # on_error itself must not take the bus down
                log.exception("tracking_detect: on_error failed")

    def _process(self, event: Event) -> None:
        data = event.data or {}
        if data.get("from_me") or data.get("sender") == "me":
            return

        message = {
            "message_id": data.get("message_id") or data.get("id") or event.id,
            "sender": data.get("sender", ""),
            "channel": data.get("channel", ""),
            "text": data.get("text", "") or "",
            "subject": data.get("subject", ""),
            "conversation_id": data.get("conversation_id", ""),
            "timestamp": data.get("timestamp") or event.timestamp,
            "from_me": False,
        }
        self.detect_message(message)

    def detect_message(self, message: dict) -> int:
        """Run detection over one normalized message dict; return #candidates.

        The single detection path shared by both bus front-ends: the
        eventd/EventBus route (``_process`` above) and the live CommsBus
        route (``core.comms.consumers.tracking_detect``). Keep detection
        logic here so the two front-ends can never drift apart.
        """
        from qareen.tracking import detect as _detect

        config = self._get_config()
        store = self._get_store()
        result = _detect.detect(
            message, self._get_packs(), store=store, config=config
        )
        if result.skipped_reason:
            log.debug(
                "tracking_detect: skipped %s message (%s)",
                message.get("channel", ""), result.skipped_reason,
            )
            return 0
        if not result.candidates:
            return 0

        counts = _detect.persist(result, store, config)
        for cand in result.candidates:
            log.info(
                "tracking_detect: %s %s conf=%.2f layer=%s from %s/%s",
                cand.carrier,
                cand.tracking_number,
                cand.confidence,
                cand.layer,
                message.get("channel", ""),
                message.get("sender", ""),
            )
        log.debug("tracking_detect: persisted %s", counts)
        return len(result.candidates)

    def health(self) -> dict:
        return {
            "name": self.name,
            "handles": self.handles,
            "packs_loaded": sorted(self._packs) if self._packs else None,
            "store_available": self._store is not None,
            "probe_enabled": bool(self._config and self._config.probe_enabled),
            "llm_enabled": bool(self._config and self._config.llm_enabled),
        }
