"""iMessage watcher — detects new messages by monitoring chat.db changes.

Watches the WAL (Write-Ahead Log) file for chat.db. macOS writes to the
WAL on every new message, so monitoring its mtime gives us near-instant
detection without expensive database queries.

When a change is detected:
1. Read new messages via iMessage adapter
2. Publish comms.message_received events to system bus
3. Track last-seen timestamp to avoid re-processing
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..watcher import BaseWatcher

log = logging.getLogger(__name__)

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
CHAT_WAL = CHAT_DB.parent / (CHAT_DB.name + "-wal")
POLL_INTERVAL = 2  # seconds


class iMessageWatcher(BaseWatcher):
    """Watches iMessage chat.db for new messages."""

    name = "imessage"
    domain = "comms"

    def __init__(self):
        super().__init__()
        self._last_mtime: float = 0
        self._last_message_ts: datetime = datetime.now()
        self._adapter = None

    def _get_adapter(self):
        """Lazy-load the iMessage adapter."""
        if self._adapter is None:
            try:
                from core.comms.channels.imessage import iMessageAdapter
                self._adapter = iMessageAdapter()
            except ImportError:
                log.error("iMessage adapter not available")
        return self._adapter

    def start(self) -> None:
        """Watch chat.db WAL for changes."""
        log.info("iMessage watcher started (polling %s every %ds)",
                 CHAT_WAL, POLL_INTERVAL)

        # Initialize with current mtime
        self._last_mtime = self._get_mtime()
        # Start from now — don't process historical messages
        self._last_message_ts = datetime.now()

        while self._running:
            try:
                current_mtime = self._get_mtime()
                if current_mtime > self._last_mtime:
                    self._last_mtime = current_mtime
                    self._on_change()
            except Exception as e:
                log.error("iMessage watcher error: %s", e)

            time.sleep(POLL_INTERVAL)

    def _get_mtime(self) -> float:
        """Get the modification time of the WAL file (or DB if no WAL)."""
        try:
            if CHAT_WAL.exists():
                return os.path.getmtime(CHAT_WAL)
            elif CHAT_DB.exists():
                return os.path.getmtime(CHAT_DB)
        except OSError:
            pass
        return 0

    def _on_change(self) -> None:
        """Called when chat.db changes — read new messages and publish."""
        adapter = self._get_adapter()
        if not adapter or not adapter.is_available():
            return

        try:
            messages = adapter.get_messages(since=self._last_message_ts)
        except Exception as e:
            log.error("Failed to read new iMessages: %s", e)
            return

        if not messages:
            return

        # Update last seen timestamp
        self._last_message_ts = max(m.timestamp for m in messages) + timedelta(seconds=1)

        # Publish each message as an event
        from core.bus import system_bus, Event

        for msg in messages:
            system_bus.publish(Event(
                type="comms.message_received",
                data={
                    "channel": "imessage",
                    "sender": msg.sender,
                    "text": msg.text[:500],
                    "conversation_id": msg.conversation_id,
                    "from_me": msg.from_me,
                    "timestamp": msg.timestamp.isoformat(),
                },
                source="imessage_watcher",
            ))

        log.info("iMessage: %d new message(s) published", len(messages))

    def stop(self) -> None:
        """No resources to clean up."""
        pass

    def health(self) -> dict:
        wal_exists = CHAT_WAL.exists()
        db_exists = CHAT_DB.exists()
        return {
            "name": self.name,
            "running": self.is_running,
            "db_exists": db_exists,
            "wal_exists": wal_exists,
            "last_mtime": self._last_mtime,
            "last_message_ts": self._last_message_ts.isoformat(),
            "poll_interval": POLL_INTERVAL,
        }
