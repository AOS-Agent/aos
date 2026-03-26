"""WhatsApp watcher — polls the whatsmeow bridge for new messages.

The whatsmeow bridge receives WhatsApp messages in real-time via the
WhatsApp Web protocol. This watcher polls the bridge's /messages endpoint
periodically and publishes new messages to the system bus.

Graceful skip: if the bridge isn't running or WhatsApp isn't configured,
the watcher logs a warning and goes dormant. It checks availability
on each cycle, so it automatically activates when the bridge comes online.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from ..watcher import BaseWatcher

log = logging.getLogger(__name__)

BRIDGE_URL = "http://127.0.0.1:7601"
POLL_INTERVAL = 5  # seconds


class WhatsAppWatcher(BaseWatcher):
    """Watches WhatsApp via the whatsmeow bridge HTTP API."""

    name = "whatsapp"
    domain = "comms"

    def __init__(self):
        super().__init__()
        self._last_message_ts: datetime = datetime.now()
        self._adapter = None
        self._consecutive_failures = 0

    def _get_adapter(self):
        """Lazy-load the WhatsApp adapter."""
        if self._adapter is None:
            try:
                from core.comms.channels.whatsapp import WhatsAppAdapter
                self._adapter = WhatsAppAdapter()
            except ImportError:
                log.warning("WhatsApp adapter not available")
        return self._adapter

    def _bridge_available(self) -> bool:
        """Check if the whatsmeow bridge is running and connected."""
        try:
            resp = urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=3)
            data = json.loads(resp.read())
            return data.get("connected", False)
        except Exception:
            return False

    def start(self) -> None:
        """Poll the bridge for new messages."""
        log.info("WhatsApp watcher started (polling bridge every %ds)", POLL_INTERVAL)

        # Start from now
        self._last_message_ts = datetime.now()

        while self._running:
            try:
                if not self._bridge_available():
                    if self._consecutive_failures == 0:
                        log.info("WhatsApp bridge not available — waiting")
                    self._consecutive_failures += 1
                    # Back off: check less frequently when bridge is down
                    time.sleep(min(POLL_INTERVAL * self._consecutive_failures, 60))
                    continue

                self._consecutive_failures = 0
                self._poll_messages()

            except Exception as e:
                log.error("WhatsApp watcher error: %s", e)

            time.sleep(POLL_INTERVAL)

    def _poll_messages(self) -> None:
        """Read new messages from the bridge and publish events."""
        adapter = self._get_adapter()
        if not adapter:
            return

        try:
            messages = adapter.get_messages(since=self._last_message_ts)
        except Exception as e:
            log.error("Failed to read WhatsApp messages: %s", e)
            return

        if not messages:
            return

        # Update timestamp
        self._last_message_ts = max(m.timestamp for m in messages) + timedelta(seconds=1)

        # Publish to system bus
        from core.bus import system_bus, Event

        for msg in messages:
            system_bus.publish(Event(
                type="comms.message_received",
                data={
                    "channel": "whatsapp",
                    "sender": msg.sender,
                    "text": msg.text[:500],
                    "conversation_id": msg.conversation_id,
                    "from_me": msg.from_me,
                    "timestamp": msg.timestamp.isoformat(),
                },
                source="whatsapp_watcher",
            ))

        log.info("WhatsApp: %d new message(s) published", len(messages))

    def stop(self) -> None:
        pass

    def health(self) -> dict:
        bridge_up = self._bridge_available()
        return {
            "name": self.name,
            "running": self.is_running,
            "bridge_available": bridge_up,
            "consecutive_failures": self._consecutive_failures,
            "last_message_ts": self._last_message_ts.isoformat(),
            "poll_interval": POLL_INTERVAL,
        }
