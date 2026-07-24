"""Notification consumer — routes system events to the operator.

Subscribes to `notify.*` events on the system bus and delivers them via
the topic-aware notify router (core/engine/notify). Any domain can
notify the operator:

    system_bus.publish(Event("notify.send", data={"text": "Task completed"}))
    system_bus.publish(Event("notify.alert", data={"text": "Disk 90% full"}))
    system_bus.publish(Event("notify.send", data={"text": "Saved", "topic": "knowledge"}))

Event types:
    notify.send    — Normal notification
    notify.alert   — Urgent (routes to the alerts forum topic)
    notify.success — Completion (✅ prefix)
    notify.info    — Informational (ℹ️ prefix)

Routing: events may declare a forum topic via data["topic"]
(daily/alerts/work/knowledge/system). Undeclared events route by kind:
alert -> alerts topic, everything else -> system topic. Delivery falls
back topic -> group General -> operator DM (see notify/router.py).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..consumer import EventConsumer
from ..event import Event

sys.path.insert(0, str(Path.home() / "aos" / "core" / "engine"))
from notify.router import send_notification  # noqa: E402

log = logging.getLogger(__name__)


class NotificationConsumer(EventConsumer):
    """Routes notify.* events to Telegram via the notify router."""

    name = "notification"
    handles = ["notify.*"]

    def process(self, event: Event) -> None:
        """Process a notification event."""
        text = event.data.get("text", "")
        if not text:
            return

        # Pass the action through as kind: alert/success/info gain their
        # emoji prefix and routing; "send" (and anything else) stays
        # unprefixed and routes to the system topic.
        result = send_notification(
            text,
            topic=event.data.get("topic"),
            kind=event.action or "send",
        )
        if result["delivered"]:
            log.debug("Notification delivered -> %s", result["target"])
        else:
            log.info("Notification not delivered (%s): %s",
                     result["error"], text[:100])
