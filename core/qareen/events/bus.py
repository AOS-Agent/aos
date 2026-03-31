"""Qareen Event Bus — Async in-memory publish/subscribe.

The EventBus is the central nervous system of the Qareen runtime.
Actions emit events, and any component can subscribe to event types
to react. Handlers are async callables invoked concurrently.

This is one of the few skeleton files with a real implementation
because the bus itself is simple (~50 lines of core logic) and
every other component depends on it working.

Usage:
    bus = EventBus()
    bus.subscribe("task.created", my_handler)
    await bus.emit(TaskCreated(task_id="aos#42", title="Build bus"))
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

from .types import Event

logger = logging.getLogger(__name__)

# Type alias for async event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async in-memory event bus with wildcard support.

    Supports exact event_type subscriptions ("task.created") and
    wildcard prefix subscriptions ("task.*" matches all task events).

    Handlers are invoked concurrently via asyncio.gather. A failing
    handler logs the error but does not prevent other handlers from
    running.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_limit: int = 1000

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register an async handler for an event type.

        Args:
            event_type: Exact type ("task.created") or wildcard ("task.*").
                        Use "*" to subscribe to all events.
            handler: An async callable that accepts a single Event argument.
        """
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a previously registered handler.

        Args:
            event_type: The event type the handler was registered under.
            handler: The handler to remove.

        Raises:
            ValueError: If the handler was not subscribed to this event type.
        """
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            raise ValueError(
                f"Handler {handler!r} is not subscribed to '{event_type}'"
            )

    async def emit(self, event: Event) -> None:
        """Publish an event to all matching subscribers.

        Matching rules:
          1. Exact match on event.event_type
          2. Wildcard match: "task.*" matches "task.created", "task.updated", etc.
          3. Global wildcard "*" matches everything

        All matched handlers run concurrently. Exceptions are logged but
        do not propagate (fire-and-forget semantics).

        Args:
            event: The event to publish.
        """
        # Record in history
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        # Collect matching handlers
        handlers: list[EventHandler] = []

        # Exact match
        handlers.extend(self._handlers.get(event.event_type, []))

        # Wildcard match (e.g. "task.*" matches "task.created")
        if "." in event.event_type:
            prefix = event.event_type.rsplit(".", 1)[0]
            handlers.extend(self._handlers.get(f"{prefix}.*", []))

        # Global wildcard
        handlers.extend(self._handlers.get("*", []))

        if not handlers:
            return

        # Run all handlers concurrently
        results = await asyncio.gather(
            *(self._safe_call(handler, event) for handler in handlers),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    "Event handler failed for '%s': %s",
                    event.event_type,
                    result,
                    exc_info=result,
                )

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Invoke a handler with error isolation.

        Args:
            handler: The async handler to call.
            event: The event to pass.
        """
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "Handler %r failed processing %s", handler, event.event_type
            )
            raise

    def handler_count(self, event_type: str | None = None) -> int:
        """Return the number of registered handlers.

        Args:
            event_type: If provided, count only handlers for this type.
                        If None, count all handlers across all types.

        Returns:
            Number of registered handlers.
        """
        if event_type is not None:
            return len(self._handlers.get(event_type, []))
        return sum(len(h) for h in self._handlers.values())

    def recent_events(self, limit: int = 50) -> list[Event]:
        """Return the most recent events from the in-memory history.

        Args:
            limit: Maximum number of events to return.

        Returns:
            List of events, most recent last.
        """
        return self._history[-limit:]

    def clear(self) -> None:
        """Remove all handlers and clear event history."""
        self._handlers.clear()
        self._history.clear()
