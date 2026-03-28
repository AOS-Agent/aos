"""System event bus — the nervous system of AOS.

Singleton bus that all domains publish to and consume from. Events flow
between domains without them knowing about each other.

Usage:
    from core.bus import system_bus, Event

    # Publish
    system_bus.publish(Event("comms.message_received", data={
        "channel": "whatsapp",
        "sender": "+15551234567",
        "text": "Hello",
    }, source="whatsapp_adapter"))

    # Subscribe
    class MyConsumer(EventConsumer):
        name = "my_consumer"
        handles = ["comms.*"]
        def process(self, event):
            print(f"Got: {event.type}")

    system_bus.subscribe(MyConsumer())

Design:
    - Synchronous delivery (V1). Async/queue-based delivery is future.
    - Consumer errors are isolated — one failing consumer doesn't block others.
    - Singleton via module-level `system_bus` instance.
    - Event log kept in memory (last N events) for debugging.
"""

from __future__ import annotations

import logging
from collections import deque

from .consumer import EventConsumer
from .event import Event

log = logging.getLogger(__name__)

MAX_EVENT_LOG = 500  # Keep last N events in memory for debugging


class SystemBus:
    """Domain-agnostic event bus.

    Producers call publish(). Consumers subscribe and receive events
    matching their `handles` patterns.
    """

    def __init__(self):
        self._consumers: list[EventConsumer] = []
        self._event_log: deque[Event] = deque(maxlen=MAX_EVENT_LOG)
        self._stats: dict[str, int] = {}  # event_type -> count

    def subscribe(self, consumer: EventConsumer) -> None:
        """Register a consumer to receive events."""
        self._consumers.append(consumer)
        log.info("Subscribed: %s (handles: %s)", consumer.name, consumer.handles)

    def unsubscribe(self, consumer_name: str) -> bool:
        """Remove a consumer by name."""
        before = len(self._consumers)
        self._consumers = [c for c in self._consumers if c.name != consumer_name]
        removed = len(self._consumers) < before
        if removed:
            log.info("Unsubscribed: %s", consumer_name)
        return removed

    def publish(self, event: Event) -> int:
        """Publish an event to all matching consumers.

        Args:
            event: The event to publish.

        Returns:
            Number of consumers that received the event.
        """
        self._event_log.append(event)
        self._stats[event.type] = self._stats.get(event.type, 0) + 1

        delivered = 0
        for consumer in self._consumers:
            if not consumer.accepts(event):
                continue
            try:
                consumer.process(event)
                delivered += 1
            except Exception as e:
                consumer.on_error(e, event)

        log.debug(
            "Published %s → %d/%d consumers",
            event.type, delivered, len(self._consumers),
        )
        return delivered

    def publish_many(self, events: list[Event]) -> int:
        """Publish multiple events. Returns total deliveries."""
        total = 0
        for event in events:
            total += self.publish(event)
        return total

    @property
    def consumers(self) -> list[EventConsumer]:
        return list(self._consumers)

    @property
    def recent_events(self) -> list[Event]:
        """Last N events for debugging."""
        return list(self._event_log)

    @property
    def stats(self) -> dict[str, int]:
        """Event type counts since bus creation."""
        return dict(self._stats)

    def health(self) -> dict:
        return {
            "consumers": len(self._consumers),
            "consumer_names": [c.name for c in self._consumers],
            "events_total": sum(self._stats.values()),
            "events_by_type": dict(sorted(
                self._stats.items(), key=lambda x: -x[1]
            )[:10]),
            "event_log_size": len(self._event_log),
        }

    def reset(self) -> None:
        """Clear all consumers and event log. For testing."""
        self._consumers.clear()
        self._event_log.clear()
        self._stats.clear()

    def __repr__(self) -> str:
        return (
            f"<SystemBus consumers={len(self._consumers)} "
            f"events={sum(self._stats.values())}>"
        )


# ── Singleton ────────────────────────────────────────────
# Import this everywhere: from core.bus import system_bus
system_bus = SystemBus()
