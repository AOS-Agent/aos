"""Messaging Actions — Send messages to people via their preferred channel.

Routes messages through WhatsApp (whatsmeow on :7601) or Telegram (Bot API).
Stores all sent messages in comms.db for history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from qareen.events.actions import action

logger = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Channel endpoints
WHATSMEOW_URL = "http://127.0.0.1:7601/send"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict, timeout: int = 10) -> dict:
    """POST JSON to a URL using stdlib. Returns parsed response or error dict."""
    data = json.dumps(payload).encode()
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"ok": True, "raw": body}
    except URLError as e:
        return {"ok": False, "error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _store_message(
    channel: str,
    recipient_id: str,
    person_id: str,
    person_name: str,
    text: str,
    channel_id: str,
    delivery_result: dict,
) -> str:
    """Store a sent message in comms.db. Returns the message id."""
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()

    # Ensure or find conversation
    conv_id = _ensure_conversation(channel, person_id, person_name)

    try:
        db = sqlite3.connect(str(COMMS_DB))
        db.execute("PRAGMA journal_mode=WAL")

        db.execute(
            """INSERT INTO messages
               (id, channel, direction, sender_id, recipient_id,
                content, timestamp, person_id, conversation_id,
                channel_metadata)
               VALUES (?, ?, 'outbound', ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                channel,
                "operator",
                channel_id,
                text,
                now,
                person_id,
                conv_id,
                json.dumps(delivery_result),
            ),
        )

        # Update conversation stats
        db.execute(
            """UPDATE conversations
               SET last_message_at = ?, message_count = message_count + 1
               WHERE id = ?""",
            (now, conv_id),
        )

        db.commit()
        db.close()
    except Exception as e:
        logger.warning("Failed to store sent message in comms.db: %s", e)
        return msg_id

    return msg_id


def _ensure_conversation(channel: str, person_id: str, person_name: str) -> str:
    """Find or create a conversation for this person+channel pair."""
    try:
        db = sqlite3.connect(str(COMMS_DB))
        db.execute("PRAGMA journal_mode=WAL")

        row = db.execute(
            "SELECT id FROM conversations WHERE person_id = ? AND channel = ?",
            (person_id, channel),
        ).fetchone()

        if row:
            conv_id = row[0]
        else:
            conv_id = f"conv_{uuid.uuid4().hex[:12]}"
            db.execute(
                """INSERT INTO conversations
                   (id, channel, person_id, name, status, message_count, unread_count)
                   VALUES (?, ?, ?, ?, 'open', 0, 0)""",
                (conv_id, channel, person_id, person_name),
            )
            db.commit()

        db.close()
        return conv_id
    except Exception as e:
        logger.warning("Failed to ensure conversation: %s", e)
        return f"conv_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Channel delivery
# ---------------------------------------------------------------------------

def _send_whatsapp(channel_id: str, text: str) -> dict:
    """Send a message via WhatsApp (whatsmeow service on :7601)."""
    return _http_post(WHATSMEOW_URL, {"to": channel_id, "text": text})


def _send_email(channel_id: str, text: str) -> dict:
    """Placeholder for email sending. Returns a not-implemented result."""
    return {"ok": False, "error": "Email sending not yet implemented"}


# ---------------------------------------------------------------------------
# Main action
# ---------------------------------------------------------------------------

@action("send_message", emits="message.sent")
async def send_message(
    ontology,
    recipient: str,
    text: str,
    channel: str | None = None,
    **kwargs,
) -> dict:
    """Send a message to a person via their preferred channel.

    1. Resolve person name to channel info via people adapter
    2. Route to the right channel (whatsmeow for WhatsApp)
    3. Store the sent message in comms.db
    4. Return result with delivery status
    """
    # 1. Resolve person
    channel_info = ontology.resolve_channel(recipient)
    if not channel_info:
        return {
            "success": False,
            "error": f"Could not find '{recipient}' or no messaging channel available",
        }

    # Override channel if specified
    resolved_channel = channel or channel_info["channel"]
    channel_id = channel_info["channel_id"]
    person_id = channel_info["person_id"]
    person_name = channel_info["person_name"]

    # 2. Route to channel
    if resolved_channel == "whatsapp":
        result = _send_whatsapp(channel_id, text)
    elif resolved_channel == "email":
        result = _send_email(channel_id, text)
    else:
        result = {"ok": False, "error": f"Unsupported channel: {resolved_channel}"}

    delivered = result.get("ok", False) or result.get("success", False)

    # 3. Store in comms.db
    msg_id = _store_message(
        channel=resolved_channel,
        recipient_id=channel_id,
        person_id=person_id,
        person_name=person_name,
        text=text,
        channel_id=channel_id,
        delivery_result=result,
    )

    # 4. Return result
    return {
        "success": delivered,
        "message_id": msg_id,
        "recipient": person_name,
        "channel": resolved_channel,
        "channel_id": channel_id,
        "delivery": result,
        "stored": True,
    }
