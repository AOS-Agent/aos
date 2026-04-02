"""WhatsApp bridge watcher — monitors whatsmeow bridge health for send capability.

The whatsmeow bridge is used for SENDING messages (drafts, autonomous replies).
Message DETECTION is handled by the WhatsAppLocalWatcher (reads ChatStorage.sqlite).

This watcher's job:
1. Track whether the bridge is available for sending
2. Publish bridge health status so the draft/autonomous systems know if send is possible
3. Does NOT publish comms.message_received — that's the local watcher's job

If the local watcher is not available (no desktop app), this watcher falls back to
publishing message events from the bridge. But that's the backup path.
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

    def _local_watcher_active(self) -> bool:
        """Check if the WhatsApp local watcher is running (it's the primary)."""
        from pathlib import Path
        wa_db = (
            Path.home() / "Library" / "Group Containers"
            / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
        )
        return wa_db.exists()

    def start(self) -> None:
        """Monitor bridge health. Only publish messages if local watcher is unavailable."""
        self._fallback_mode = not self._local_watcher_active()
        mode = "FALLBACK (publishing messages)" if self._fallback_mode else "SEND-ONLY (local watcher handles receive)"
        log.info("WhatsApp bridge watcher started — %s, polling every %ds", mode, POLL_INTERVAL)

        self._last_message_ts = datetime.now()

        while self._running:
            try:
                if not self._bridge_available():
                    if self._consecutive_failures == 0:
                        log.info("WhatsApp bridge not available — waiting")
                    self._consecutive_failures += 1
                    time.sleep(min(POLL_INTERVAL * self._consecutive_failures, 60))
                    continue

                self._consecutive_failures = 0

                # Only poll messages if local watcher is NOT available (fallback mode)
                if self._fallback_mode:
                    self._poll_messages()

            except Exception as e:
                log.error("WhatsApp bridge watcher error: %s", e)

            time.sleep(POLL_INTERVAL)

    def _poll_messages(self) -> None:
        """Fallback: read messages from bridge when no local watcher."""
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

        self._last_message_ts = max(m.timestamp for m in messages) + timedelta(seconds=1)

        from core.bus import Event, system_bus

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
                source="whatsapp_watcher_fallback",
            ))

        log.info("WhatsApp (fallback): %d new message(s) published", len(messages))

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
