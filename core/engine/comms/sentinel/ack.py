"""In-thread acknowledgement — fires when a trigger is detected.

Strategies:
  - "notification" (default): macOS notification on the Mac. No in-thread signal.
  - "tapback": Best-effort iMessage tapback (👍) on the operator's trigger message
                via AppleScript UI scripting. Fragile — requires accessibility perms
                and Messages.app to be openable. May fail silently.
  - "emoji": Send a separate emoji message into the chat. Visible to recipient (noisy).
  - "off": No acknowledgement at all.

Configured per-deployment in ~/.aos/config/sentinel.yaml → `ack_method`.

Why no real tapback API:
- Messages.app AppleScript dictionary has NO documented tapback support.
- Private IMCore framework would require pyobjc + reverse-engineered selectors.
- macOS Shortcuts has no built-in "Send Tapback" action.
- UI scripting is the only path — and it disrupts focus + can break on OS updates.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

ACK_EMOJI = "👍"
IMESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"
CONFIG_PATH = Path.home() / ".aos" / "config" / "sentinel.yaml"


def _config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _ack_method() -> str:
    return str(_config().get("ack_method", "notification")).lower()


def is_ack_message(text: Optional[str]) -> bool:
    """True if a message body is purely our ack emoji (loop prevention)."""
    if not text:
        return False
    return text.strip() == ACK_EMOJI


def resolve_imessage_recipient(conversation_id: Optional[str]) -> Optional[str]:
    """Map comms.db conversation_id → recipient handle via chat.db (read-only)."""
    if not conversation_id or not IMESSAGE_DB.exists():
        return None
    try:
        chat_rowid = int(conversation_id)
    except (TypeError, ValueError):
        return None
    uri = f"file:{IMESSAGE_DB}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        row = conn.execute("""
            SELECT h.id FROM chat_handle_join chj
            JOIN handle h ON chj.handle_id = h.ROWID
            WHERE chj.chat_id = ? LIMIT 1
        """, (chat_rowid,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        log.debug("imessage recipient resolve failed: %s", e)
        return None


def _resolve_chat_guid(conversation_id: Optional[str]) -> Optional[str]:
    """Get the chat.guid for a chat rowid — used by UI scripting to address the chat."""
    if not conversation_id or not IMESSAGE_DB.exists():
        return None
    try:
        chat_rowid = int(conversation_id)
    except (TypeError, ValueError):
        return None
    uri = f"file:{IMESSAGE_DB}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        row = conn.execute("SELECT guid FROM chat WHERE rowid = ?",
                           (chat_rowid,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _try_notification_ack(recipient: Optional[str], task_hint: str = "") -> bool:
    """Fire a macOS notification — no in-thread footprint."""
    try:
        from .notifier import notify
        notify(
            title="Sentinel received trigger",
            message=f"Working on it for {recipient or 'unknown'}",
            subtitle=task_hint[:80] if task_hint else "",
            sound="Tink",
        )
        return True
    except Exception as e:
        log.error("notification ack failed: %s", e)
        return False


def _try_tapback_ack(conversation_id: Optional[str]) -> bool:
    """Best-effort iMessage 👍 tapback via UI scripting.

    Opens Messages.app, navigates to the chat by handle, selects the last
    outgoing message, sends the thumbs-up tapback. Fragile — requires:
    - accessibility permission for the script's parent process
    - Messages.app installed and signed-in to the iMessage account
    - The chat to be findable by handle

    Returns True only if the script completed without error. Doesn't guarantee
    the tapback actually appeared.
    """
    recipient = resolve_imessage_recipient(conversation_id)
    if not recipient:
        log.warning("tapback: no recipient resolved for conv=%s", conversation_id)
        return False

    # AppleScript: activate Messages, focus the chat by handle, navigate to last
    # outgoing message, simulate the tapback shortcut (Cmd+T in modern macOS).
    safe_recipient = recipient.replace('"', '\\"')
    script = f'''
        tell application "Messages"
            activate
        end tell
        delay 0.3
        tell application "System Events"
            tell process "Messages"
                -- Send Cmd+K to open the "New Message" / search field
                keystroke "k" using {{command down, shift down}}
                delay 0.3
                keystroke "{safe_recipient}"
                delay 0.4
                key code 36 -- Return
                delay 0.4
                -- Move focus to the transcript and select the last outgoing message
                key code 125 using {{command down}} -- Cmd+Down
                delay 0.2
                key code 125 -- Down arrow to the last bubble
                delay 0.2
                -- Open Tapback menu — Cmd+T (works on macOS 14+)
                keystroke "t" using {{command down}}
                delay 0.3
                -- Thumbs up is the second tapback (after heart)
                key code 124 -- Right
                key code 124 -- Right (to thumbs up)
                key code 36  -- Return to confirm
            end tell
        end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info("tapback attempted for %s (no error)", recipient)
            return True
        log.warning("tapback osascript rc=%d stderr=%s",
                    result.returncode, result.stderr.strip()[:120])
        return False
    except subprocess.TimeoutExpired:
        log.warning("tapback timed out")
        return False
    except Exception as e:
        log.error("tapback error: %s", e)
        return False


def _try_emoji_ack(channel: str, conversation_id: Optional[str]) -> bool:
    """Send 👍 as a separate iMessage — noisy but reliable."""
    if channel != "imessage":
        return False
    recipient = resolve_imessage_recipient(conversation_id)
    if not recipient:
        return False
    try:
        from core.engine.comms.channels.imessage import iMessageAdapter
        return iMessageAdapter().send_message(recipient, ACK_EMOJI)
    except Exception as e:
        log.error("emoji ack send error: %s", e)
        return False


def send_ack(channel: str, conversation_id: Optional[str],
             recipient_hint: Optional[str] = None,
             task_hint: str = "") -> bool:
    """Dispatch the configured ack method. Returns True on success."""
    method = _ack_method()
    log.debug("send_ack method=%s channel=%s conv=%s", method, channel, conversation_id)

    if method == "off":
        return True
    if method == "notification":
        recipient = recipient_hint or (resolve_imessage_recipient(conversation_id)
                                        if channel == "imessage" else None)
        return _try_notification_ack(recipient, task_hint)
    if method == "tapback":
        # Try tapback first, fall back to notification (NEVER to emoji)
        ok = _try_tapback_ack(conversation_id)
        if not ok:
            log.info("tapback failed — falling back to macOS notification")
            recipient = recipient_hint or resolve_imessage_recipient(conversation_id)
            return _try_notification_ack(recipient, task_hint)
        return True
    if method == "emoji":
        return _try_emoji_ack(channel, conversation_id)
    log.warning("Unknown ack_method=%s — skipping", method)
    return False
