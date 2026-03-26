"""Event consumer base class.

Consumers subscribe to the system bus and react to events they care about.
Each consumer declares which event types it handles via the `handles` attribute.

Pattern matching:
    "comms.message_received"  — exact match
    "comms.*"                 — all comms events
    "*"                       — all events (firehose)
    "*.message_received"      — message_received from any domain
"""

from __future__ import annotations

import fnmatch
import logging
from abc import ABC, abstractmethod

from .event import Event

log = logging.getLogger(__name__)


class EventConsumer(ABC):
    """Base class for system bus consumers.

    Subclasses set `name` and `handles`, then implement `process()`.
    The bus calls `accepts()` to filter events before delivery.
    """

    name: str = ""
    handles: list[str] = []  # Event type patterns this consumer cares about

    def accepts(self, event: Event) -> bool:
        """Check if this consumer handles the given event type.

        Uses fnmatch-style pattern matching against the handles list.
        """
        if not self.handles:
            return False
        return any(fnmatch.fnmatch(event.type, pattern) for pattern in self.handles)

    @abstractmethod
    def process(self, event: Event) -> None:
        """Process a single event.

        Called by the bus for each event that passes `accepts()`.
        Should be fast — don't do heavy work inline. Queue it if needed.

        Args:
            event: The event to process.
        """
        ...

    def on_error(self, error: Exception, event: Event | None = None) -> None:
        """Handle a processing error. Override for custom error handling."""
        log.error(
            "Consumer %s error on %s: %s",
            self.name,
            event.type if event else "unknown",
            error,
            exc_info=True,
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} [{self.name}] handles={self.handles}>"
