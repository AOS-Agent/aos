"""People Intelligence system consumer.

Subscribes to the system bus for events from any domain that involve
person interactions. Currently handles comms events; future domains
(schedule, email) add handlers here.

This consumer bridges the system bus to the existing People Intelligence
DB. It doesn't duplicate the comms-level consumer's message processing —
instead it reacts to higher-level domain events.

Event types handled:
    comms.message_sent     — operator sent a message (log outbound interaction)
    comms.messages_polled  — messages received (logged by comms consumer already,
                             but system consumer tracks the signal)
    schedule.*             — future: calendar events as interactions
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..consumer import EventConsumer
from ..event import Event

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"


def _get_people_db():
    """Lazy import of the People Intelligence DB module."""
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        return people_db
    except ImportError:
        return None


class PeopleIntelSystemConsumer(EventConsumer):
    """System-level People Intelligence consumer.

    Reacts to events from any domain that involve person interactions.
    Keeps the People DB's relationship_state and intelligence_queue
    informed about cross-domain activity.
    """

    name = "people_intel_system"
    handles = [
        "comms.message_sent",      # Outbound messages
        "comms.messages_polled",    # Inbound message batches
        "schedule.*",              # Future: meetings, calendar events
        "people.*",                # People domain events
    ]

    def __init__(self):
        self._db_mod = None

    @property
    def db(self):
        if self._db_mod is None:
            self._db_mod = _get_people_db()
        return self._db_mod

    def process(self, event: Event) -> None:
        """Route event to the appropriate handler."""
        handler = {
            "comms.message_sent": self._handle_message_sent,
            "comms.messages_polled": self._handle_messages_polled,
        }.get(event.type)

        if handler:
            handler(event)
        elif event.domain == "schedule":
            self._handle_schedule_event(event)
        else:
            log.debug("People Intel system: unhandled event %s", event.type)

    def _handle_message_sent(self, event: Event) -> None:
        """Track that the operator sent a message."""
        channel = event.data.get("channel", "unknown")
        recipient = event.data.get("recipient", "unknown")
        log.info(
            "People Intel: outbound on %s to %s",
            channel, recipient[:20]
        )
        # The comms-level consumer handles the actual DB write
        # via the outbound Message object. This handler is for
        # system-level signals (e.g., updating relationship_state
        # trajectory, surfacing reconnect opportunities).

    def _handle_messages_polled(self, event: Event) -> None:
        """React to a batch of messages being polled."""
        count = event.data.get("count", 0)
        channels = event.data.get("channels", {})
        if count > 0:
            log.debug(
                "People Intel: %d messages polled from %s",
                count, channels
            )

    def _handle_schedule_event(self, event: Event) -> None:
        """Handle calendar/schedule events as person interactions.

        Future: when the schedule domain emits events like
        schedule.meeting_started with attendee data, this handler
        will log them as interactions in the People DB.
        """
        log.debug(
            "People Intel: schedule event %s (handler ready, not yet wired)",
            event.action
        )
