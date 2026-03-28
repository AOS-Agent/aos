"""AOS Unified Communication Layer.

Provides a unified interface for reading messages across all communication
channels (WhatsApp, iMessage, email, etc.) through a common adapter pattern.

Architecture:
    core/comms/
        models.py       — Message, Conversation data models
        channel.py      — ChannelAdapter base class
        registry.py     — Discovers active channels from integrations registry
        bus.py          — Unified message stream (Phase 4)
        channels/       — Adapter implementations per channel
        consumers/      — Services that subscribe to the bus
"""
