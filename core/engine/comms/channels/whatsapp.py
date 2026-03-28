"""WhatsApp channel adapter.

Wraps the existing whatsapp_client.py (HTTP API to whatsmeow bridge)
behind the ChannelAdapter interface. Read-only in V1.

The whatsmeow bridge runs on 127.0.0.1:7601 and provides:
    GET  /health          — connection status
    GET  /messages?days=N — fetch recent messages
    GET  /chats           — list known chats
    POST /send            — send (deferred to V2)
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from ..channel import ChannelAdapter
from ..models import Conversation, Message

BRIDGE_URL = "http://127.0.0.1:7601"
DEFAULT_DAYS = 1


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp adapter via whatsmeow HTTP bridge."""

    name = "whatsapp"
    display_name = "WhatsApp"
    can_send = True
    can_receive = True

    def __init__(self, base_url: str = BRIDGE_URL):
        self.base_url = base_url.rstrip("/")

    # --- Lifecycle ---

    def is_available(self) -> bool:
        """Check if the whatsmeow bridge is running and connected."""
        try:
            resp = urllib.request.urlopen(f"{self.base_url}/health", timeout=5)
            data = json.loads(resp.read())
            return data.get("connected", False)
        except Exception:
            return False

    def health(self) -> dict:
        try:
            resp = urllib.request.urlopen(f"{self.base_url}/health", timeout=5)
            data = json.loads(resp.read())
            return {
                "available": data.get("connected", False),
                "channel": self.name,
                **data,
            }
        except Exception as e:
            return {"available": False, "channel": self.name, "error": str(e)}

    # --- Read interface ---

    def get_conversations(self, since: datetime | None = None) -> list[Conversation]:
        """Get conversations from the WhatsApp bridge.

        Uses /chats endpoint. Since filtering is approximate — the bridge
        returns all known chats, we filter client-side.
        """
        try:
            resp = urllib.request.urlopen(f"{self.base_url}/chats", timeout=10)
            chats = json.loads(resp.read())
        except Exception:
            return []

        if not chats:
            return []

        conversations = []
        for chat in chats:
            conv = Conversation(
                id=chat.get("jid", chat.get("id", "")),
                channel=self.name,
                name=chat.get("name", chat.get("jid", "Unknown")),
                participants=[],  # WhatsApp chats endpoint doesn't list participants
                last_message_at=_parse_timestamp(chat.get("last_message_time")),
                message_count=chat.get("unread", 0),
                metadata={
                    "jid": chat.get("jid"),
                    "is_group": chat.get("is_group", False),
                },
            )

            # Filter by since if provided
            if since and conv.last_message_at and conv.last_message_at < since:
                continue

            conversations.append(conv)

        return conversations

    def get_messages(
        self,
        conversation_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """Get messages from the WhatsApp bridge.

        Uses /messages endpoint. Translates the bridge's day-based windowing
        to the since-based interface.
        """
        # Calculate days from since
        if since:
            delta = datetime.now() - since
            days = max(1, delta.days + 1)
        else:
            days = DEFAULT_DAYS

        url = f"{self.base_url}/messages?days={days}"
        if conversation_id:
            url += f"&chat={urllib.parse.quote(conversation_id)}"

        try:
            resp = urllib.request.urlopen(url, timeout=30)
            raw_messages = json.loads(resp.read())
        except Exception:
            return []

        if not raw_messages:
            return []

        messages = []
        for raw in raw_messages:
            ts = _parse_timestamp(raw.get("timestamp"))
            if not ts:
                continue

            # Filter by since
            if since and ts < since:
                continue

            sender = raw.get("sender", "Unknown")
            from_me = raw.get("from_me", False) or sender.lower() in ("me", "you")

            msg = Message(
                id=raw.get("id", f"wa-{ts.timestamp():.0f}"),
                channel=self.name,
                conversation_id=raw.get("chat", conversation_id or "unknown"),
                sender="me" if from_me else sender,
                text=raw.get("text", ""),
                timestamp=ts,
                from_me=from_me,
                metadata={
                    k: v for k, v in raw.items()
                    if k not in ("text", "timestamp", "sender", "chat", "id", "from_me")
                },
            )
            messages.append(msg)

        # Sort by timestamp
        messages.sort(key=lambda m: m.timestamp)

        if limit:
            messages = messages[:limit]

        return messages

    def resolve_handle(self, handle: str) -> str | None:
        """Resolve a WhatsApp handle (JID or name) to a phone number.

        WhatsApp JIDs are formatted as: {phone}@s.whatsapp.net (personal)
        or {id}@g.us (group). Extract the phone number from personal JIDs.
        """
        if not handle:
            return None

        # Already a phone number
        if re.match(r'^\+?\d{7,15}$', handle.replace(" ", "").replace("-", "")):
            return _normalize_phone(handle)

        # JID format: 15551234567@s.whatsapp.net
        if "@s.whatsapp.net" in handle:
            phone = handle.split("@")[0]
            return _normalize_phone(phone)

        # Group JID — can't resolve to a single phone
        if "@g.us" in handle:
            return None

        return None

    # --- Send ---

    def send_message(self, recipient: str, text: str) -> bool:
        """Send a WhatsApp message via the whatsmeow bridge.

        Args:
            recipient: Phone number (e.g., "+15551234567") or JID.
            text: Message body.

        Returns:
            True if the bridge accepted the message.
        """
        # Normalize: strip + and non-digits for the bridge
        to = re.sub(r'[^\d]', '', recipient)
        if not to:
            return False

        data = json.dumps({"to": to, "text": text}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/send",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read())
            return bool(result.get("success", True))
        except Exception:
            return False


def _parse_timestamp(ts: Any) -> datetime | None:
    """Parse various timestamp formats from the bridge.

    Always returns a naive (timezone-unaware) local datetime for consistent
    comparison with other naive datetimes.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        # Strip timezone info for consistency
        return ts.replace(tzinfo=None) if ts.tzinfo else ts
    if isinstance(ts, (int, float)):
        # Unix timestamp
        if ts > 1e12:  # milliseconds
            ts = ts / 1000
        return datetime.fromtimestamp(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # Convert to local naive datetime
            if dt.tzinfo:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    return None


def _normalize_phone(phone: str) -> str:
    """Normalize a phone number to digits only, with leading +."""
    digits = re.sub(r'[^\d+]', '', phone)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits
