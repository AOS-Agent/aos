"""AOS System Event Bus.

Domain-agnostic event bus for cross-domain communication.
Every domain (comms, people, schedule, work, knowledge) produces and
consumes events through this bus.

Usage:
    from core.bus import system_bus, Event

    # Publish an event
    system_bus.publish(Event("comms.message_received", data={...}))

    # Subscribe a consumer
    system_bus.subscribe(my_consumer)
"""

from .bus import SystemBus, system_bus
from .consumer import EventConsumer
from .event import Event

__all__ = ["Event", "SystemBus", "system_bus", "EventConsumer"]
