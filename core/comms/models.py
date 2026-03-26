"""Unified data models for the communication layer.

These models normalize messages and conversations across all channels
into a common format. Channel-specific data goes in the metadata dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    """A single message from any channel.

    Attributes:
        id: Unique message identifier (channel-specific format).
        channel: Source channel name ("whatsapp", "imessage", "email", etc.).
        conversation_id: Identifier for the conversation this belongs to.
        sender: Sender identifier — phone number, email, handle, or "me".
        text: Message body text.
        timestamp: When the message was sent (UTC-aware preferred).
        from_me: Whether the operator sent this message.
        metadata: Channel-specific data (service type, read status, attachments, etc.).
    """
    id: str
    channel: str
    conversation_id: str
    sender: str
    text: str
    timestamp: datetime
    from_me: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def time_str(self) -> str:
        """HH:MM formatted time."""
        return self.timestamp.strftime("%H:%M")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "sender": self.sender,
            "text": self.text,
            "timestamp": self.timestamp.isoformat(),
            "from_me": self.from_me,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            id=data["id"],
            channel=data["channel"],
            conversation_id=data["conversation_id"],
            sender=data["sender"],
            text=data["text"],
            timestamp=ts,
            from_me=data.get("from_me", False),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Conversation:
    """A conversation (chat thread) from any channel.

    Attributes:
        id: Unique conversation identifier (channel-specific format).
        channel: Source channel name.
        name: Display name for the conversation (contact name, group name, etc.).
        participants: List of participant identifiers (phone numbers, handles, etc.).
        last_message_at: Timestamp of most recent message.
        message_count: Number of messages in the queried window.
        metadata: Channel-specific data (group info, chat type, etc.).
    """
    id: str
    channel: str
    name: str
    participants: list[str] = field(default_factory=list)
    last_message_at: datetime | None = None
    message_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "name": self.name,
            "participants": self.participants,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "message_count": self.message_count,
            "metadata": self.metadata,
        }
