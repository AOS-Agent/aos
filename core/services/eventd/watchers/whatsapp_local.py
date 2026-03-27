"""WhatsApp local watcher — detects new messages from the desktop app's ChatStorage.sqlite.

Monitors the WAL file for ChatStorage.sqlite. The WhatsApp desktop app writes
to this on every incoming/outgoing message, giving near-instant detection.

This is MORE RELIABLE than the whatsmeow bridge because:
- The desktop app is always running (if WhatsApp is open)
- It receives ALL messages (whatsmeow can disconnect)
- No separate linked device to maintain

When a change is detected:
1. Read new messages via WhatsAppLocalAdapter
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

WA_DB = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared"
    / "ChatStorage.sqlite"
)
WA_WAL = WA_DB.parent / (WA_DB.name + "-wal")
POLL_INTERVAL = 5  # seconds — WhatsApp doesn't need 2s, 5s is fine


class WhatsAppLocalWatcher(BaseWatcher):
    """Watches WhatsApp desktop's ChatStorage.sqlite for new messages."""

    name = "whatsapp_local"
    domain = "comms"

    def __init__(self):
        super().__init__()
        self._last_mtime: float = 0
        self._last_message_ts: datetime = datetime.now()
        self._adapter = None

    def _get_adapter(self):
        """Lazy-load the WhatsApp local adapter."""
        if self._adapter is None:
            try:
                from core.comms.channels.whatsapp_local import WhatsAppLocalAdapter
                self._adapter = WhatsAppLocalAdapter()
            except ImportError:
                log.error("WhatsApp local adapter not available")
        return self._adapter

    def start(self) -> None:
        """Watch ChatStorage.sqlite WAL for changes."""
        if not WA_DB.exists():
            log.info("WhatsApp desktop not installed — watcher inactive")
            return

        log.info("WhatsApp local watcher started (polling %s every %ds)",
                 WA_WAL, POLL_INTERVAL)

        self._last_mtime = self._get_mtime()
        self._last_message_ts = datetime.now()

        while self._running:
            try:
                current_mtime = self._get_mtime()
                if current_mtime > self._last_mtime:
                    self._last_mtime = current_mtime
                    self._on_change()
            except Exception as e:
                log.error("WhatsApp local watcher error: %s", e)

            time.sleep(POLL_INTERVAL)

    def _get_mtime(self) -> float:
        """Get modification time of the WAL file (or DB if no WAL)."""
        try:
            if WA_WAL.exists():
                return os.path.getmtime(WA_WAL)
            elif WA_DB.exists():
                return os.path.getmtime(WA_DB)
        except OSError:
            pass
        return 0

    def _on_change(self) -> None:
        """Called when ChatStorage changes — read new messages and publish."""
        adapter = self._get_adapter()
        if not adapter or not adapter.is_available():
            return

        try:
            messages = adapter.get_messages(since=self._last_message_ts)
        except Exception as e:
            log.error("Failed to read new WhatsApp messages: %s", e)
            return

        if not messages:
            return

        # Filter to only truly new messages (not from_me to avoid echo)
        inbound = [m for m in messages if not m.from_me]

        # Update last seen timestamp
        self._last_message_ts = max(m.timestamp for m in messages) + timedelta(seconds=1)

        if not inbound:
            return

        # Transcribe voice messages before publishing
        try:
            from core.comms.transcribe import transcribe_message_if_needed
            for msg in inbound:
                if msg.needs_transcription:
                    transcribe_message_if_needed(msg)
        except ImportError:
            pass  # Transcriber not available — voice stays untranscribed

        # Publish each inbound message as an event
        from core.bus import system_bus, Event

        for msg in inbound:
            system_bus.publish(Event(
                type="comms.message_received",
                data={
                    "channel": "whatsapp",
                    "sender": msg.sender,
                    "text": (msg.text or "")[:500],
                    "conversation_id": msg.conversation_id,
                    "from_me": msg.from_me,
                    "timestamp": msg.timestamp.isoformat(),
                    "media_type": msg.media_type,
                    "media_path": msg.media_path,
                },
                source="whatsapp_local_watcher",
            ))

        log.info("WhatsApp local: %d new message(s) published (%d voice, %d media)",
                 len(inbound),
                 sum(1 for m in inbound if m.media_type == "voice"),
                 sum(1 for m in inbound if m.has_media))

    def stop(self) -> None:
        pass

    def health(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running,
            "db_exists": WA_DB.exists(),
            "wal_exists": WA_WAL.exists(),
            "last_mtime": self._last_mtime,
            "last_message_ts": self._last_message_ts.isoformat(),
            "poll_interval": POLL_INTERVAL,
        }
