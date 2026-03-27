"""Unified message bus.

Loads active channel adapters from the integrations registry, polls them
for messages, and delivers the unified stream to registered consumers.

V1 is polling-only. Event-driven bus is a future iteration.

Usage:
    from core.comms.bus import MessageBus
    from core.comms.consumers.people_intel import PeopleIntelConsumer

    bus = MessageBus()
    bus.register_consumer(PeopleIntelConsumer())
    messages = bus.poll()  # Polls all active adapters, delivers to consumers
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from .models import Message
from .registry import load_adapters

# System event bus — comms publishes here so other domains can react
try:
    from core.bus import system_bus, Event as SystemEvent
    _HAS_SYSTEM_BUS = True
except ImportError:
    _HAS_SYSTEM_BUS = False

log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 1


class Consumer(ABC):
    """Base class for message bus consumers.

    Consumers subscribe to the bus and receive unified messages from
    all active channels. Each consumer implements its own processing
    logic (index, store, notify, etc.).
    """

    name: str = ""

    @abstractmethod
    def process(self, messages: list[Message]) -> int:
        """Process a batch of messages.

        Args:
            messages: List of unified Message objects from all channels.

        Returns:
            Number of messages successfully processed.
        """
        ...

    def on_error(self, error: Exception, message: Message | None = None) -> None:
        """Handle a processing error. Override for custom error handling."""
        log.error(f"Consumer {self.name} error: {error}", exc_info=True)


class MessageBus:
    """Unified message bus that polls adapters and delivers to consumers.

    The bus is the central coordination point:
    1. Discovers active channels from the integrations registry
    2. Polls each channel adapter for messages
    3. Merges messages into a single time-ordered stream
    4. Delivers the stream to all registered consumers
    """

    def __init__(self, auto_register: bool = True):
        self.consumers: list[Consumer] = []
        self._adapters = None  # Lazy-loaded
        self._last_poll: datetime | None = None

        if auto_register:
            self._register_default_consumers()

    def _register_default_consumers(self):
        """Register all standard consumers. Failures are non-fatal."""
        _consumer_classes = [
            ("core.comms.consumers.people_intel", "PeopleIntelConsumer"),
            ("core.comms.consumers.pattern_update", "PatternUpdateConsumer"),
            ("core.comms.orchestrator", "CommsOrchestrator"),
        ]
        for mod_path, cls_name in _consumer_classes:
            try:
                import importlib
                mod = importlib.import_module(mod_path)
                consumer = getattr(mod, cls_name)()
                self.register_consumer(consumer)
            except Exception as e:
                log.debug(f"Could not load consumer {cls_name}: {e}")

    @property
    def adapters(self):
        """Lazy-load adapters from the registry."""
        if self._adapters is None:
            self._adapters = load_adapters()
        return self._adapters

    def register_consumer(self, consumer: Consumer) -> None:
        """Register a consumer to receive messages."""
        self.consumers.append(consumer)
        log.info(f"Registered consumer: {consumer.name}")

    def poll(
        self,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """Poll all active adapters and deliver messages to consumers.

        Args:
            since: Only messages after this time. Defaults to last poll time
                   or DEFAULT_WINDOW_DAYS ago.
            limit: Max total messages across all channels.

        Returns:
            Unified list of messages, sorted by timestamp.
        """
        if since is None:
            since = self._last_poll or (
                datetime.now() - timedelta(days=DEFAULT_WINDOW_DAYS)
            )

        all_messages: list[Message] = []
        adapter_results: dict[str, int] = {}

        for adapter in self.adapters:
            # Skip unavailable adapters gracefully
            if not adapter.is_available():
                adapter_results[adapter.name] = 0
                log.debug(f"Skipping unavailable adapter: {adapter.name}")
                continue

            try:
                messages = adapter.get_messages(since=since)
                all_messages.extend(messages)
                adapter_results[adapter.name] = len(messages)
                log.debug(f"{adapter.name}: {len(messages)} messages")
            except Exception as e:
                adapter_results[adapter.name] = 0
                log.error(f"Error polling {adapter.name}: {e}")

        # Sort by timestamp across all channels
        all_messages.sort(key=lambda m: m.timestamp)

        if limit:
            all_messages = all_messages[:limit]

        # Deliver to consumers
        for consumer in self.consumers:
            try:
                processed = consumer.process(all_messages)
                log.debug(f"Consumer {consumer.name}: processed {processed}/{len(all_messages)}")
            except Exception as e:
                consumer.on_error(e)

        self._last_poll = datetime.now()

        # Publish to system bus so other domains can react
        if _HAS_SYSTEM_BUS and all_messages:
            system_bus.publish(SystemEvent(
                type="comms.messages_polled",
                data={
                    "count": len(all_messages),
                    "channels": dict(adapter_results),
                    "window_start": since.isoformat() if since else None,
                },
                source="comms_bus",
            ))

        return all_messages

    def send(
        self,
        recipient: str,
        text: str,
        channel: str,
    ) -> bool:
        """Send a message through the bus.

        Routes to the appropriate adapter and notifies consumers of
        the outbound message. This is the preferred way to send —
        consumers see both inbound and outbound.

        Args:
            recipient: Channel-specific recipient (phone, email, JID).
            text: Message body.
            channel: Channel name ("whatsapp", "imessage", etc.).

        Returns:
            True if sent successfully.
        """
        # Find the adapter for this channel
        adapter = None
        for a in self.adapters:
            if a.name == channel:
                adapter = a
                break

        if not adapter:
            log.error(f"No adapter found for channel: {channel}")
            return False

        if not adapter.can_send:
            log.error(f"{adapter.display_name} adapter does not support sending")
            return False

        if not adapter.is_available():
            log.error(f"{adapter.display_name} is not available")
            return False

        # Send via adapter
        try:
            success = adapter.send_message(recipient, text)
        except Exception as e:
            log.error(f"Send failed on {channel}: {e}")
            return False

        if not success:
            return False

        # Create outbound message for consumers
        outbound = Message(
            id=f"out-{channel}-{datetime.now().timestamp():.0f}",
            channel=channel,
            conversation_id=recipient,
            sender="me",
            text=text,
            timestamp=datetime.now(),
            from_me=True,
            metadata={"direction": "outbound"},
        )

        # Notify consumers
        for consumer in self.consumers:
            try:
                consumer.process([outbound])
            except Exception as e:
                consumer.on_error(e, outbound)

        # Publish to system bus
        if _HAS_SYSTEM_BUS:
            system_bus.publish(SystemEvent(
                type="comms.message_sent",
                data={
                    "channel": channel,
                    "recipient": recipient,
                    "text": text[:200],
                },
                source="comms_bus",
            ))

        log.info(f"Sent on {channel} to {recipient}: {text[:50]}")
        return True

    def health(self) -> dict:
        """Return health status for the bus and all adapters."""
        adapter_health = {}
        for adapter in self.adapters:
            adapter_health[adapter.name] = adapter.health()

        return {
            "adapters": adapter_health,
            "adapters_total": len(self.adapters),
            "adapters_available": sum(
                1 for h in adapter_health.values() if h.get("available")
            ),
            "consumers": [c.name for c in self.consumers],
            "last_poll": self._last_poll.isoformat() if self._last_poll else None,
        }

    def reload_adapters(self) -> None:
        """Force reload adapters from the registry."""
        self._adapters = None
        _ = self.adapters  # Trigger reload

    def __repr__(self) -> str:
        return (
            f"<MessageBus adapters={len(self.adapters)} "
            f"consumers={len(self.consumers)}>"
        )
