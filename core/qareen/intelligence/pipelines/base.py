"""Pipeline base class for Qareen background processors.

Each pipeline subscribes to EventBus events and produces actionable output
(new events, context store writes, or side effects). Pipelines must never
crash or propagate exceptions — all errors are logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qareen.events.bus import EventBus
    from qareen.intelligence.context_store import QareenContextStore

logger = logging.getLogger(__name__)


class Pipeline:
    """Base class for all Qareen background pipelines.

    Subclasses must implement wire() to subscribe to bus events.
    """

    def __init__(
        self,
        bus: EventBus,
        context_store: QareenContextStore | None = None,
    ) -> None:
        self._bus = bus
        self._context = context_store

    def wire(self) -> None:
        """Subscribe to bus events. Call after init."""
        raise NotImplementedError
