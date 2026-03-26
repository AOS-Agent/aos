"""Base channel adapter interface.

Every communication channel implements this interface. Adapters are
bidirectional: read messages via get_messages(), send via send_message().
Send goes through the bus (bus.send()) so consumers observe both directions.

Adapters wrap existing readers (whatsapp_client.py, imessage_reader.py, etc.)
without rewriting them. Handle resolution delegates to the People Intelligence
contact resolver — adapters don't solve identity themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .models import Conversation, Message


class ChannelAdapter(ABC):
    """Base class for all communication channel adapters.

    Subclasses must implement the read methods. The adapter knows how to
    talk to its source (SQLite DB, HTTP API, IMAP, etc.) and normalize
    results into the unified Message/Conversation models.
    """

    # --- Class attributes: subclasses must set these ---

    name: str = ""            # Channel identifier: "whatsapp", "imessage", etc.
    display_name: str = ""    # Human-readable: "WhatsApp", "iMessage", etc.
    can_send: bool = False    # Subclass sets True when send is implemented
    can_receive: bool = True  # All adapters can read messages

    # --- Lifecycle ---

    def is_available(self) -> bool:
        """Check if this channel's data source is accessible.

        Returns True if the underlying service/database/API is reachable.
        Used by the bus to skip unavailable channels gracefully.
        """
        return True

    def health(self) -> dict:
        """Return health status for this channel.

        Returns:
            Dict with at minimum {"available": bool}. Adapters can add
            channel-specific health info (connection status, last sync, etc.).
        """
        available = self.is_available()
        return {"available": available, "channel": self.name}

    # --- Read interface (required) ---

    @abstractmethod
    def get_conversations(self, since: datetime | None = None) -> list[Conversation]:
        """Get conversations with activity since the given timestamp.

        Args:
            since: Only return conversations with messages after this time.
                   If None, return recent conversations (adapter decides window).

        Returns:
            List of Conversation objects.
        """
        ...

    @abstractmethod
    def get_messages(
        self,
        conversation_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """Get messages, optionally filtered by conversation and time.

        Args:
            conversation_id: Filter to a specific conversation. None = all.
            since: Only messages after this timestamp. None = adapter default.
            limit: Max messages to return. None = no limit.

        Returns:
            List of Message objects, ordered by timestamp ascending.
        """
        ...

    @abstractmethod
    def resolve_handle(self, handle: str) -> str | None:
        """Resolve a channel-specific handle to a normalized identifier.

        Maps channel handles (WhatsApp JID, iMessage phone/email, etc.)
        to a normalized form suitable for People Intelligence lookup.

        Args:
            handle: Channel-specific identifier.

        Returns:
            Normalized identifier (phone number, email), or None if
            resolution fails.
        """
        ...

    # --- Send interface ---

    def send_message(self, recipient: str, text: str) -> bool:
        """Send a message through this channel.

        Note: Callers should use bus.send() instead of calling this directly,
        so consumers (People Intel, etc.) observe outbound messages.

        Args:
            recipient: Channel-specific recipient (phone number, JID, email).
            text: Message body.

        Returns:
            True if sent successfully, False otherwise.

        Raises:
            NotImplementedError: If adapter hasn't implemented send.
        """
        raise NotImplementedError(
            f"{self.display_name} adapter does not support sending."
        )

    # --- Utility ---

    def __repr__(self) -> str:
        mode = "r/w" if self.can_send else "r/o"
        return f"<{self.__class__.__name__} [{self.name}] ({mode})>"
