"""Notification consumer — routes system events to the operator.

Subscribes to `notify.*` events on the system bus and delivers them
via Telegram Bot API. Any domain can notify the operator:

    system_bus.publish(Event("notify.send", data={"text": "Task completed"}))
    system_bus.publish(Event("notify.alert", data={"text": "Disk 90% full"}))

Event types:
    notify.send   — Normal notification (plain text)
    notify.alert  — Urgent notification (prefixed with ⚠️)

Credentials: Telegram bot token and chat ID from macOS Keychain
via agent-secret. Gracefully skips if not configured.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request

from ..consumer import EventConsumer
from ..event import Event

log = logging.getLogger(__name__)


def _get_secret(name: str) -> str | None:
    """Read a secret from macOS Keychain via agent-secret."""
    try:
        result = subprocess.run(
            [str(__import__("pathlib").Path.home() / "aos" / "core" / "bin" / "agent-secret"), "get", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip()
        return value if value and result.returncode == 0 else None
    except Exception:
        return None


class NotificationConsumer(EventConsumer):
    """Routes notify.* events to Telegram."""

    name = "notification"
    handles = ["notify.*"]

    def __init__(self):
        self._token: str | None = None
        self._chat_id: str | None = None
        self._loaded = False

    def _load_credentials(self):
        """Lazy-load Telegram credentials from Keychain."""
        if self._loaded:
            return
        self._token = _get_secret("TELEGRAM_BOT_TOKEN")
        self._chat_id = _get_secret("TELEGRAM_CHAT_ID")
        self._loaded = True
        if not self._token or not self._chat_id:
            log.warning(
                "Telegram credentials not configured — notifications will be logged only. "
                "Set via: agent-secret set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
            )

    @property
    def is_configured(self) -> bool:
        self._load_credentials()
        return bool(self._token and self._chat_id)

    def process(self, event: Event) -> None:
        """Process a notification event."""
        text = event.data.get("text", "")
        if not text:
            return

        # Format based on event type
        if event.action == "alert":
            text = f"⚠️ {text}"
        elif event.action == "success":
            text = f"✅ {text}"
        elif event.action == "info":
            text = f"ℹ️ {text}"

        # Add source context if present
        source = event.data.get("source") or event.source
        if source:
            text = f"{text}\n\n— {source}"

        # Send via Telegram
        if self.is_configured:
            self._send_telegram(text)
        else:
            log.info("Notification (no Telegram): %s", text[:100])

    def _send_telegram(self, text: str) -> bool:
        """Send a message via Telegram Bot API."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        data = json.dumps({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": False,
        }).encode()

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read())
            if result.get("ok"):
                log.debug("Telegram notification sent")
                return True
            else:
                log.error("Telegram API error: %s", result)
                return False
        except urllib.error.HTTPError as e:
            log.error("Telegram send failed (%d): %s", e.code, e.read().decode()[:200])
            return False
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            return False

    def health(self) -> dict:
        return {
            "name": self.name,
            "configured": self.is_configured,
            "handles": self.handles,
        }
