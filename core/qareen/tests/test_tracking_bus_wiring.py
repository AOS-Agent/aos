"""Wiring tests: is tracking detection actually connected to the live bus?

These tests exist because Auto Tracker v0.7.0 shipped with a complete,
well-tested detection pipeline that was connected to nothing. Every existing
detect test constructs the consumer directly with injected dependencies —
proving the class works, and proving nothing about whether the running system
ever calls it. It didn't, for a full release.

So these assert the *seam*, not the logic:
  1. the live MessageBus actually registers a tracking consumer
  2. the CommsBus Message → detector dict translation is correct
  3. the adapter is isolated (one bad message can't sink a batch)

Rule of thumb for anything added here: if the test would still pass with the
consumer unregistered, it belongs in test_tracking_detect.py instead.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

# Package root is core/; repo root is its parent (for `core.comms.*`).
_CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(_CORE.parent))

from core.comms.consumers.tracking_detect import (  # noqa: E402
    TrackingDetectConsumer,
)
from core.comms.models import Message  # noqa: E402


def _msg(**kw):
    base = dict(
        id="m1",
        channel="email",
        conversation_id="c1",
        sender="ship@carrier.example",
        text="Tracking: 1Z999AA10123456784",
        timestamp=datetime.now(timezone.utc),
        from_me=False,
        metadata={},
    )
    base.update(kw)
    return Message(**base)


class _FakeDetector:
    """Records the dicts handed to detect_message."""

    def __init__(self, explode_on=None):
        self.seen = []
        self._explode_on = explode_on

    def detect_message(self, message):
        if self._explode_on and message.get("message_id") == self._explode_on:
            raise RuntimeError("boom")
        self.seen.append(message)
        return 1


# ── 1. the seam that was missing ─────────────────────────────────────────


def test_tracking_consumer_is_registered_on_the_live_bus():
    """The regression guard for the v0.7.0 defect.

    MessageBus._register_default_consumers is the list the running comms
    service uses. If tracking_detect falls off it, detection goes dark
    silently — the bus keeps working and nothing else fails.
    """
    from core.comms.bus import MessageBus

    bus = MessageBus()
    names = [c.name for c in bus.consumers]
    assert "tracking_detect" in names, (
        "tracking_detect is not registered on MessageBus — detection is dark. "
        f"Registered: {names}"
    )


def test_registered_consumer_satisfies_the_commsbus_contract():
    """CommsBus calls process(list[Message]) -> int.

    The EventBus consumer of the same name takes a single Event, so
    registering *that* class here would not raise — it would be handed a
    list, fail inside its own never-raise guard, and detect nothing
    forever. This asserts the registered object speaks CommsBus.
    """
    from core.comms.bus import MessageBus

    bus = MessageBus()
    consumer = next(c for c in bus.consumers if c.name == "tracking_detect")

    detector = _FakeDetector()
    consumer._detector = detector

    result = consumer.process([_msg()])
    assert isinstance(result, int)
    assert len(detector.seen) == 1


# ── 2. translation correctness ───────────────────────────────────────────


def test_subject_is_carried_from_metadata():
    """Message has no `subject` field — email adapters put it in metadata.

    Detection weights subject lines heavily for order confirmations, so
    dropping it would blind the primary tracking channel.
    """
    detector = _FakeDetector()
    consumer = TrackingDetectConsumer(detector=detector)

    consumer.process([_msg(metadata={"subject": "Your order has shipped"})])

    assert detector.seen[0]["subject"] == "Your order has shipped"


def test_message_fields_map_onto_detector_keys():
    detector = _FakeDetector()
    consumer = TrackingDetectConsumer(detector=detector)

    ts = datetime.now(timezone.utc)
    consumer.process([_msg(id="abc", channel="imessage", sender="+15551234", timestamp=ts)])

    got = detector.seen[0]
    assert got["message_id"] == "abc"
    assert got["channel"] == "imessage"
    assert got["sender"] == "+15551234"
    assert got["timestamp"] == ts
    assert got["from_me"] is False


# ── 3. isolation posture ─────────────────────────────────────────────────


def test_outbound_messages_are_skipped():
    detector = _FakeDetector()
    consumer = TrackingDetectConsumer(detector=detector)

    consumer.process([_msg(from_me=True), _msg(id="m2", sender="me")])

    assert detector.seen == []


def test_empty_messages_are_skipped():
    detector = _FakeDetector()
    consumer = TrackingDetectConsumer(detector=detector)

    consumer.process([_msg(text="", metadata={})])

    assert detector.seen == []


def test_one_bad_message_does_not_sink_the_batch():
    """Detection is best-effort enrichment; it must never abort ingestion."""
    detector = _FakeDetector(explode_on="bad")
    consumer = TrackingDetectConsumer(detector=detector)

    inspected = consumer.process(
        [_msg(id="good1"), _msg(id="bad"), _msg(id="good2")]
    )

    assert inspected == 2
    assert [m["message_id"] for m in detector.seen] == ["good1", "good2"]


def test_detector_is_not_constructed_when_batch_has_nothing_to_inspect():
    """All-outbound batches must not pay pack-loading cost."""
    consumer = TrackingDetectConsumer()
    assert consumer.process([_msg(from_me=True)]) == 0
    assert consumer._detector is None
