"""System event model.

Events are the unit of communication between AOS domains. Every significant
thing that happens — message received, task completed, meeting starting,
notification requested — is an event.

Event types use domain prefix convention:
    comms.message_received
    comms.message_sent
    schedule.event_starting
    work.task_completed
    people.interaction_logged
    notify.send
    notify.alert
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Event:
    """A system event from any domain.

    Attributes:
        type: Event type with domain prefix (e.g., "comms.message_received").
        data: Event payload — domain-specific, consumers know the schema.
        source: What produced this event (e.g., "whatsapp_adapter", "work_engine").
        timestamp: When the event occurred.
        id: Unique event ID (auto-generated if not provided).
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"evt-{self.timestamp.timestamp():.0f}-{id(self) % 10000}"

    @property
    def domain(self) -> str:
        """Extract domain from event type (e.g., 'comms' from 'comms.message_received')."""
        return self.type.split(".")[0] if "." in self.type else ""

    @property
    def action(self) -> str:
        """Extract action from event type (e.g., 'message_received' from 'comms.message_received')."""
        parts = self.type.split(".", 1)
        return parts[1] if len(parts) > 1 else self.type

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "domain": self.domain,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }

    def __repr__(self) -> str:
        return f"<Event {self.type} from={self.source}>"
