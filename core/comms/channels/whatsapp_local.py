"""WhatsApp local adapter — reads from ChatStorage.sqlite.

The whatsmeow bridge only has messages from when it was connected.
The WhatsApp desktop app stores the FULL message history locally in
ChatStorage.sqlite. This adapter reads that directly — 6+ years of data.

Used by the extraction pipeline for retroactive history. The primary
WhatsApp adapter (whatsapp.py) still uses the bridge for live messages.

Location: ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
Timestamps: Apple epoch (seconds since 2001-01-01, offset 978307200)
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from ..channel import ChannelAdapter
from ..models import Conversation, Message

# WhatsApp desktop stores data here
WA_DB_PATH = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared"
    / "ChatStorage.sqlite"
)

# Apple epoch offset: seconds between 1970-01-01 and 2001-01-01
APPLE_EPOCH = 978307200
DEFAULT_DAYS = 1


class WhatsAppLocalAdapter(ChannelAdapter):
    """WhatsApp adapter reading from the desktop app's local SQLite database."""

    name = "whatsapp_local"
    display_name = "WhatsApp (Local History)"
    can_send = False   # Read-only — sending goes through the bridge adapter
    can_receive = True

    def __init__(self, db_path: Path = WA_DB_PATH):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection | None:
        """Copy DB to temp location and connect (avoids locking live DB)."""
        if not self.db_path.exists():
            return None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
            tmp.close()
            shutil.copy2(str(self.db_path), tmp.name)
            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            return conn
        except (PermissionError, OSError):
            return None

    def _from_apple_ts(self, ts: float | None) -> datetime | None:
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(ts + APPLE_EPOCH)
        except (OSError, OverflowError, ValueError):
            return None

    def _to_apple_ts(self, dt: datetime) -> float:
        return dt.timestamp() - APPLE_EPOCH

    # --- Lifecycle ---

    def is_available(self) -> bool:
        return self.db_path.exists()

    def health(self) -> dict:
        base = {"channel": self.name}
        if not self.db_path.exists():
            return {**base, "available": False, "error": "ChatStorage.sqlite not found"}
        try:
            conn = self._connect()
            if conn:
                count = conn.execute("SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZTEXT IS NOT NULL").fetchone()[0]
                conn.close()
                return {**base, "available": True, "total_messages": count}
        except Exception as e:
            return {**base, "available": False, "error": str(e)}
        return {**base, "available": False}

    # --- Read interface ---

    def get_conversations(self, since: datetime | None = None) -> list[Conversation]:
        conn = self._connect()
        if not conn:
            return []

        try:
            cutoff = self._to_apple_ts(since) if since else self._to_apple_ts(
                datetime.now() - timedelta(days=DEFAULT_DAYS)
            )

            rows = conn.execute("""
                SELECT
                    c.Z_PK,
                    c.ZCONTACTJID,
                    c.ZPARTNERNAME,
                    c.ZSESSIONTYPE,
                    MAX(m.ZMESSAGEDATE) as last_msg,
                    COUNT(m.Z_PK) as msg_count
                FROM ZWACHATSESSION c
                JOIN ZWAMESSAGE m ON m.ZCHATSESSION = c.Z_PK
                WHERE m.ZTEXT IS NOT NULL AND m.ZTEXT != ''
                  AND m.ZMESSAGEDATE >= ?
                GROUP BY c.Z_PK
                ORDER BY last_msg DESC
            """, (cutoff,)).fetchall()

            conversations = []
            for r in rows:
                conversations.append(Conversation(
                    id=r["ZCONTACTJID"] or str(r["Z_PK"]),
                    channel="whatsapp",  # Unified as "whatsapp" not "whatsapp_local"
                    name=r["ZPARTNERNAME"] or r["ZCONTACTJID"] or "Unknown",
                    participants=[r["ZCONTACTJID"]] if r["ZCONTACTJID"] else [],
                    last_message_at=self._from_apple_ts(r["last_msg"]),
                    message_count=r["msg_count"],
                    metadata={
                        "jid": r["ZCONTACTJID"],
                        "is_group": r["ZSESSIONTYPE"] != 0,
                    },
                ))
            return conversations
        finally:
            conn.close()

    def get_messages(
        self,
        conversation_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        conn = self._connect()
        if not conn:
            return []

        try:
            cutoff = self._to_apple_ts(since) if since else self._to_apple_ts(
                datetime.now() - timedelta(days=DEFAULT_DAYS)
            )

            query = """
                SELECT
                    m.Z_PK,
                    m.ZTEXT,
                    m.ZMESSAGEDATE,
                    m.ZISFROMME,
                    m.ZFROMJID,
                    m.ZTOJID,
                    c.ZCONTACTJID,
                    c.ZPARTNERNAME
                FROM ZWAMESSAGE m
                LEFT JOIN ZWACHATSESSION c ON m.ZCHATSESSION = c.Z_PK
                WHERE m.ZTEXT IS NOT NULL AND m.ZTEXT != ''
                  AND m.ZMESSAGEDATE >= ?
            """
            params = [cutoff]

            if conversation_id:
                query += " AND c.ZCONTACTJID = ?"
                params.append(conversation_id)

            query += " ORDER BY m.ZMESSAGEDATE"

            if limit:
                query += f" LIMIT {int(limit)}"

            rows = conn.execute(query, params).fetchall()

            messages = []
            for r in rows:
                ts = self._from_apple_ts(r["ZMESSAGEDATE"])
                if not ts:
                    continue

                from_me = bool(r["ZISFROMME"])
                # For group messages, ZFROMJID has the sender JID
                # For DMs, the partner is always the other person
                if from_me:
                    sender = "me"
                else:
                    sender = r["ZPARTNERNAME"] or r["ZFROMJID"] or "Unknown"

                jid = r["ZCONTACTJID"] or ""

                messages.append(Message(
                    id=f"wa-local-{r['Z_PK']}",
                    channel="whatsapp",  # Unified channel name
                    conversation_id=jid,
                    sender=sender,
                    text=r["ZTEXT"],
                    timestamp=ts,
                    from_me=from_me,
                    metadata={
                        "jid": jid,
                        "from_jid": r["ZFROMJID"],
                        "is_group": "@g.us" in jid if jid else False,
                    },
                ))

            return messages
        finally:
            conn.close()

    def resolve_handle(self, handle: str) -> str | None:
        if not handle:
            return None
        # JID → phone number
        if "@s.whatsapp.net" in handle:
            phone = handle.split("@")[0]
            return _normalize_phone(phone)
        if re.match(r'^\+?\d{7,15}$', handle.replace(" ", "").replace("-", "")):
            return _normalize_phone(handle)
        return None


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r'[^\d+]', '', phone)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits
