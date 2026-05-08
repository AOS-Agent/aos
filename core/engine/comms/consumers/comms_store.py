"""Comms Store consumer.

Writes every message from the bus into comms.db — the unified cross-channel
message store. This is the canonical record of all communication content.

For each message:
1. Resolve sender to a person_id (via people DB resolver)
2. Upsert the conversation record
3. INSERT the message with full content
4. FTS5 trigger auto-indexes for instant search

Deduplicates on (channel, timestamp, sender_id) to handle overlapping
poll windows safely.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

_PEOPLE_PATHS = [
    Path.home() / "aos" / "core" / "engine" / "people",
    Path.home() / "project" / "aos" / "core" / "engine" / "people",
]


def _ensure_people_path():
    for path in _PEOPLE_PATHS:
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _get_resolver():
    try:
        from .. import resolver
        return resolver
    except ImportError:
        _ensure_people_path()
        try:
            import resolver
            return resolver
        except ImportError:
            return None


def _get_people_db():
    _ensure_people_path()
    try:
        import db as people_db
        return people_db
    except ImportError:
        return None


class CommsStoreConsumer(Consumer):
    """Writes messages to comms.db — the unified message store."""

    name = "comms_store"

    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._resolver = None
        self._people_db = None
        self._people_conn = None
        self._resolve_cache: dict[str, str | None] = {}

    @property
    def conn(self) -> sqlite3.Connection | None:
        if self._conn is None:
            if not COMMS_DB.exists():
                log.warning("comms.db not found at %s", COMMS_DB)
                return None
            self._conn = sqlite3.connect(str(COMMS_DB))
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _resolve_person(self, sender: str, channel: str) -> str | None:
        """Resolve a sender handle to a person_id. Cached per session."""
        cache_key = f"{channel}:{sender}"
        if cache_key in self._resolve_cache:
            return self._resolve_cache[cache_key]

        person_id = None

        # Try full resolver
        if self._resolver is None:
            self._resolver = _get_resolver() or False
        if self._resolver and self._resolver is not False:
            if self._people_db is None:
                self._people_db = _get_people_db()
            if self._people_conn is None and self._people_db:
                self._people_conn = self._people_db.connect()
            try:
                result = self._resolver.resolve_contact(
                    sender, context=channel, conn=self._people_conn
                )
                if result and result.get("resolved"):
                    person_id = result.get("person_id")
            except Exception:
                pass

        self._resolve_cache[cache_key] = person_id
        return person_id

    def process(self, messages: list[Message]) -> int:
        """Write messages to comms.db. Returns count of new messages stored."""
        if not messages or not self.conn:
            return 0

        stored = 0
        for msg in messages:
            try:
                if self._store_message(msg):
                    stored += 1
            except Exception as e:
                log.debug("Failed to store message %s: %s", msg.id, e)

        if stored:
            self.conn.commit()
            log.info("Stored %d/%d messages in comms.db", stored, len(messages))

        return stored

    def _store_message(self, msg: Message) -> bool:
        """Store a single message. Returns True if new, False if duplicate."""
        conn = self.conn

        # Dedup: check if this exact message already exists
        ts_str = msg.timestamp.isoformat() if isinstance(msg.timestamp, datetime) else str(msg.timestamp)
        existing = conn.execute(
            "SELECT 1 FROM messages WHERE channel = ? AND timestamp = ? AND sender_id = ? LIMIT 1",
            (msg.channel, ts_str, msg.sender or ""),
        ).fetchone()
        if existing:
            return False

        # Resolve person
        person_id = None
        if msg.sender and not msg.from_me:
            person_id = self._resolve_person(msg.sender, msg.channel)
        elif msg.from_me and msg.conversation_id:
            person_id = self._resolve_person(msg.conversation_id, msg.channel)

        # Upsert conversation
        conv_id = msg.conversation_id or f"{msg.channel}:{msg.sender or 'unknown'}"
        self._upsert_conversation(conv_id, msg.channel, person_id, ts_str)

        # Generate message ID
        msg_id = msg.id or f"{msg.channel}-{int(time.time()*1000)}-{stored_counter()}"

        direction = "outbound" if msg.from_me else "inbound"

        # Map media fields to DB columns
        has_attachment = 1 if msg.has_media else 0
        attachment_type = msg.media_type if msg.has_media else None
        attachment_path = msg.media_path or None

        # Store channel-specific metadata as JSON
        import json as _json
        channel_meta = _json.dumps(msg.metadata) if msg.metadata else None

        conn.execute("""
            INSERT OR IGNORE INTO messages
                (id, channel, direction, sender_id, content, timestamp,
                 person_id, conversation_id, channel_metadata,
                 has_attachment, attachment_type, attachment_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg_id,
            msg.channel,
            direction,
            msg.sender or "",
            msg.text or "",
            ts_str,
            person_id,
            conv_id,
            channel_meta,
            has_attachment,
            attachment_type,
            attachment_path,
        ))

        return True

    def _upsert_conversation(self, conv_id: str, channel: str, person_id: str | None, ts: str):
        """Create or update a conversation record."""
        conn = self.conn
        existing = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE conversations
                SET last_message_at = MAX(COALESCE(last_message_at, ''), ?),
                    message_count = message_count + 1,
                    person_id = COALESCE(person_id, ?)
                WHERE id = ?
            """, (ts, person_id, conv_id))
        else:
            conn.execute("""
                INSERT INTO conversations (id, channel, person_id, last_message_at, message_count)
                VALUES (?, ?, ?, ?, 1)
            """, (conv_id, channel, person_id, ts))

    def on_error(self, error: Exception, message: Message | None = None) -> None:
        log.error("CommsStoreConsumer error: %s", error, exc_info=True)


_counter = 0

def stored_counter() -> int:
    global _counter
    _counter += 1
    return _counter
