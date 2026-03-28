"""iMessage channel adapter.

Wraps the existing imessage_reader.py (local SQLite database) behind the
ChannelAdapter interface. Bidirectional: reads from chat.db, sends via AppleScript.

iMessage stores all messages in ~/Library/Messages/chat.db. Requires
Full Disk Access for the process reading it.

Timestamps use Apple's nanosecond format: nanoseconds since 2001-01-01.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from ..channel import ChannelAdapter
from ..models import Conversation, Message

# Mac Absolute Time epoch offset (seconds between 2001-01-01 and 1970-01-01)
MAC_EPOCH_OFFSET = 978307200
NANOSECOND = 1_000_000_000

DB_PATH = Path.home() / "Library/Messages/chat.db"
DEFAULT_DAYS = 1


class iMessageAdapter(ChannelAdapter):
    """iMessage adapter via local chat.db SQLite database."""

    name = "imessage"
    display_name = "iMessage"
    can_send = True
    can_receive = True

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH

    # --- Lifecycle ---

    def is_available(self) -> bool:
        """Check if the iMessage database exists and is readable."""
        if not self.db_path.exists():
            return False
        try:
            # Quick test: can we open and query it?
            conn = self._connect()
            if conn:
                conn.close()
                return True
        except Exception:
            pass
        return False

    def health(self) -> dict:
        base = {"channel": self.name}
        if not self.db_path.exists():
            return {**base, "available": False, "error": "chat.db not found"}
        try:
            conn = self._connect()
            if conn:
                # Get message count as a health indicator
                cursor = conn.execute("SELECT COUNT(*) FROM message")
                count = cursor.fetchone()[0]
                conn.close()
                return {**base, "available": True, "total_messages": count}
        except PermissionError:
            return {**base, "available": False, "error": "Full Disk Access required"}
        except Exception as e:
            return {**base, "available": False, "error": str(e)}
        return {**base, "available": False}

    # --- Read interface ---

    def get_conversations(self, since: datetime | None = None) -> list[Conversation]:
        """Get iMessage conversations with recent activity."""
        conn = self._connect()
        if not conn:
            return []

        try:
            cutoff_ns = self._to_apple_ns(since) if since else self._to_apple_ns(
                datetime.now() - timedelta(days=DEFAULT_DAYS)
            )

            query = """
                SELECT
                    c.rowid,
                    c.chat_identifier,
                    c.display_name,
                    c.service_name,
                    MAX(m.date) as last_msg_date,
                    COUNT(m.rowid) as msg_count
                FROM chat c
                JOIN chat_message_join cmj ON c.rowid = cmj.chat_id
                JOIN message m ON cmj.message_id = m.rowid
                WHERE m.date >= ?
                GROUP BY c.rowid
                ORDER BY last_msg_date DESC
            """

            cursor = conn.execute(query, (cutoff_ns,))
            conversations = []

            for row in cursor:
                last_ts = self._from_apple_ns(row[4]) if row[4] else None
                conv = Conversation(
                    id=str(row[0]),
                    channel=self.name,
                    name=row[2] or row[1] or "Unknown",
                    participants=[row[1]] if row[1] else [],
                    last_message_at=last_ts,
                    message_count=row[5],
                    metadata={
                        "chat_identifier": row[1],
                        "service": row[3] or "iMessage",
                    },
                )
                conversations.append(conv)

            return conversations
        finally:
            conn.close()

    def get_messages(
        self,
        conversation_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """Get messages from the iMessage database."""
        conn = self._connect()
        if not conn:
            return []

        try:
            if since:
                cutoff_ns = self._to_apple_ns(since)
            else:
                cutoff_ns = self._to_apple_ns(
                    datetime.now() - timedelta(days=DEFAULT_DAYS)
                )

            # Include ALL message types — not just text.
            # associated_message_type != 0 means reactions/tapbacks.
            query = """
                SELECT
                    m.rowid,
                    m.text,
                    m.date AS msg_date,
                    m.is_from_me,
                    m.service,
                    h.id AS handle_id,
                    h.uncanonicalized_id AS handle_raw,
                    c.display_name AS chat_name,
                    c.chat_identifier,
                    c.rowid AS chat_rowid,
                    m.associated_message_type,
                    a.mime_type AS attachment_mime,
                    a.filename AS attachment_path,
                    a.transfer_name AS attachment_name
                FROM message m
                LEFT JOIN chat_message_join cmj ON m.rowid = cmj.message_id
                LEFT JOIN chat c ON cmj.chat_id = c.rowid
                LEFT JOIN handle h ON m.handle_id = h.rowid
                LEFT JOIN message_attachment_join maj ON m.rowid = maj.message_id
                LEFT JOIN attachment a ON maj.attachment_id = a.rowid
                WHERE m.date >= ?
            """
            params = [cutoff_ns]

            if conversation_id:
                query += " AND c.rowid = ?"
                params.append(int(conversation_id))

            query += " ORDER BY m.date"

            if limit:
                query += f" LIMIT {int(limit)}"

            cursor = conn.execute(query, params)
            messages = []
            seen_ids = set()  # Dedup: a message with multiple attachments appears multiple times

            for row in cursor:
                msg_id = row[0]
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                ts = self._from_apple_ns(row[2])
                if not ts:
                    continue

                from_me = bool(row[3])
                handle = row[6] or row[5] or "Unknown"
                text = row[1] or ""
                assoc_type = row[10] or 0
                mime = row[11] or ""
                att_path = row[12] or ""
                att_name = row[13] or ""

                # Determine media type
                if assoc_type != 0:
                    media_type = "reaction"
                elif mime.startswith("audio"):
                    media_type = "voice"
                elif mime.startswith("image"):
                    media_type = "image"
                elif mime.startswith("video"):
                    media_type = "video"
                elif mime == "text/x-vlocation":
                    media_type = "location"
                elif mime == "text/vcard":
                    media_type = "contact"
                elif mime and "pdf" in mime or "document" in mime or "zip" in mime:
                    media_type = "document"
                elif text:
                    media_type = "text"
                else:
                    media_type = "unknown"
                    continue  # Skip truly empty messages

                # Resolve attachment path (expand ~ to absolute)
                media_path = ""
                if att_path:
                    media_path = att_path.replace("~", str(Path.home()))

                msg = Message(
                    id=f"im-{msg_id}",
                    channel=self.name,
                    conversation_id=str(row[9]) if row[9] else "unknown",
                    sender="me" if from_me else handle,
                    text=text,
                    timestamp=ts,
                    from_me=from_me,
                    media_type=media_type,
                    media_path=media_path,
                    metadata={
                        "service": row[4] or "iMessage",
                        "handle_id": row[5],
                        "chat_name": row[7] or row[8] or "Unknown",
                        "chat_identifier": row[8],
                        "attachment_name": att_name,
                    },
                )
                messages.append(msg)

            return messages
        finally:
            conn.close()

    def resolve_handle(self, handle: str) -> str | None:
        """Resolve an iMessage handle to a normalized phone or email.

        iMessage handles are phone numbers (e.g., +15551234567) or
        email addresses. Both are already usable identifiers.
        """
        if not handle:
            return None

        # Phone number
        if re.match(r'^\+?\d{7,15}$', handle.replace(" ", "").replace("-", "")):
            return _normalize_phone(handle)

        # Email
        if "@" in handle and "." in handle:
            return handle.lower().strip()

        return handle

    # --- Send ---

    def send_message(self, recipient: str, text: str) -> bool:
        """Send an iMessage via AppleScript.

        Args:
            recipient: Phone number or email address.
            text: Message body.

        Returns:
            True if AppleScript executed successfully.
        """
        if not recipient or not text:
            return False

        # Escape single quotes for AppleScript
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        safe_recipient = recipient.replace("\\", "\\\\").replace('"', '\\"')

        script = f'''
            tell application "Messages"
                set targetService to 1st account whose service type = iMessage
                set targetBuddy to participant "{safe_recipient}" of targetService
                send "{safe_text}" to targetBuddy
            end tell
        '''

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False

    # --- Internal helpers ---

    def _connect(self) -> sqlite3.Connection | None:
        """Open a connection to a copy of chat.db (avoids lock conflicts)."""
        if not self.db_path.exists():
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()

        try:
            shutil.copy2(self.db_path, tmp.name)
            # Also copy WAL and SHM files for consistency
            for ext in ["-wal", "-shm"]:
                wal = self.db_path.parent / (self.db_path.name + ext)
                if wal.exists():
                    shutil.copy2(wal, tmp.name + ext)
        except PermissionError:
            Path(tmp.name).unlink(missing_ok=True)
            return None

        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        # Track tmp path for potential cleanup
        self._tmp_path = tmp.name
        return conn

    def _to_apple_ns(self, dt: datetime) -> int:
        """Convert datetime to Apple nanosecond timestamp."""
        return int((dt.timestamp() - MAC_EPOCH_OFFSET) * NANOSECOND)

    def _from_apple_ns(self, ns: int | None) -> datetime | None:
        """Convert Apple nanosecond timestamp to datetime."""
        if ns is None:
            return None
        ts_seconds = ns / NANOSECOND + MAC_EPOCH_OFFSET
        return datetime.fromtimestamp(ts_seconds)


def _normalize_phone(phone: str) -> str:
    """Normalize a phone number to digits only, with leading +."""
    digits = re.sub(r'[^\d+]', '', phone)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits
