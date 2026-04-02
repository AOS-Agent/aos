"""Telegram channel adapter.

Reads incoming messages from a JSONL queue file written by the bridge
service. Unlike WhatsApp/iMessage adapters that poll external services,
this reads our own bridge's message log — zero external calls.

Queue file: ~/.aos/data/telegram-messages.jsonl
Written by: core/services/bridge/telegram_channel.py (_handle_message)
Format: one JSON object per line, appended on each incoming message.

Send goes through the bridge's Telegram bot API.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from ..channel import ChannelAdapter
from ..models import Conversation, Message

QUEUE_PATH = Path.home() / ".aos" / "data" / "telegram-messages.jsonl"
# Bridge health endpoint (dashboard proxies bridge status)
DASHBOARD_URL = "http://127.0.0.1:4096"


class TelegramAdapter(ChannelAdapter):
    """Telegram adapter via bridge message queue."""

    name = "telegram"
    display_name = "Telegram"
    can_send = True
    can_receive = True

    def __init__(self, queue_path: Path = QUEUE_PATH):
        self.queue_path = queue_path

    # --- Lifecycle ---

    def is_available(self) -> bool:
        """Check if the bridge is running and queue file exists."""
        if not self.queue_path.exists():
            return False
        # Consider available if queue file was written to in last 24 hours
        try:
            mtime = self.queue_path.stat().st_mtime
            return (time.time() - mtime) < 86400
        except OSError:
            return False

    def health(self) -> dict:
        """Return health status for the Telegram channel."""
        result = {"available": False, "channel": self.name}
        if not self.queue_path.exists():
            result["error"] = "Queue file not found"
            return result

        try:
            stat = self.queue_path.stat()
            result["available"] = (time.time() - stat.st_mtime) < 86400
            result["queue_size_kb"] = round(stat.st_size / 1024, 1)
            result["last_message_age_s"] = round(time.time() - stat.st_mtime)

            # Count messages in last 24h
            since = datetime.now().timestamp() - 86400
            count = 0
            with open(self.queue_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("timestamp", 0) >= since:
                            count += 1
                    except json.JSONDecodeError:
                        continue
            result["messages_24h"] = count
        except OSError as e:
            result["error"] = str(e)

        return result

    # --- Read interface ---

    def get_conversations(self, since: datetime | None = None) -> list[Conversation]:
        """Get conversations by aggregating from the message queue."""
        messages = self.get_messages(since=since)
        if not messages:
            return []

        # Group by conversation_id
        convos: dict[str, list[Message]] = {}
        for msg in messages:
            cid = msg.conversation_id
            convos.setdefault(cid, []).append(msg)

        result = []
        for cid, msgs in convos.items():
            # Use the most common sender name as conversation name
            senders = [m.sender for m in msgs if not m.from_me]
            name = senders[0] if senders else cid

            result.append(Conversation(
                id=cid,
                channel=self.name,
                name=name,
                participants=list(set(m.sender for m in msgs)),
                last_message_at=max(m.timestamp for m in msgs),
                message_count=len(msgs),
                metadata={"chat_id": cid},
            ))

        return result

    def get_messages(
        self,
        conversation_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """Read messages from the JSONL queue file.

        Scans the file from the end for efficiency on large files.
        """
        if not self.queue_path.exists():
            return []

        since_ts = since.timestamp() if since else 0
        messages: list[Message] = []

        try:
            with open(self.queue_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts = record.get("timestamp", 0)
                    if ts < since_ts:
                        continue

                    # Filter by conversation
                    chat_id = str(record.get("chat_id", ""))
                    if conversation_id and chat_id != conversation_id:
                        continue

                    # Build Message
                    from_user = record.get("from_user", {})
                    sender_name = from_user.get("first_name", "")
                    if from_user.get("last_name"):
                        sender_name += f" {from_user['last_name']}"
                    sender_name = sender_name.strip() or str(from_user.get("id", "Unknown"))

                    from_me = record.get("from_me", False)

                    msg = Message(
                        id=record.get("id", f"tg-{ts:.0f}"),
                        channel=self.name,
                        conversation_id=chat_id,
                        sender="me" if from_me else sender_name,
                        text=record.get("text", ""),
                        timestamp=datetime.fromtimestamp(ts),
                        from_me=from_me,
                        metadata={
                            "telegram_user_id": from_user.get("id"),
                            "username": from_user.get("username"),
                            "thread_id": record.get("thread_id"),
                        },
                    )
                    messages.append(msg)
        except OSError:
            return []

        messages.sort(key=lambda m: m.timestamp)

        if limit:
            messages = messages[-limit:]

        return messages

    def resolve_handle(self, handle: str) -> str | None:
        """Resolve a Telegram handle to a username or user ID.

        Telegram handles: @username or numeric user ID.
        """
        if not handle:
            return None

        # Already a username
        if handle.startswith("@"):
            return handle.lower()

        # Numeric user ID
        if handle.isdigit():
            return handle

        return None

    # --- Send ---

    def send_message(self, recipient: str, text: str) -> bool:
        """Send a Telegram message via the bot API.

        Uses the stored bot token and chat ID from secrets.
        """
        try:
            import subprocess
            result = subprocess.run(
                [str(Path.home() / "aos" / "core" / "bin" / "agent-secret"), "get", "TELEGRAM_BOT_TOKEN"],
                capture_output=True, text=True, timeout=5,
            )
            token = result.stdout.strip()
            if not token:
                return False

            # Use recipient as chat_id
            chat_id = recipient
            data = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            }).encode()

            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            result_data = json.loads(resp.read())
            return result_data.get("ok", False)
        except Exception:
            return False
