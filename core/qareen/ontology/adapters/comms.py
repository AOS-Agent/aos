"""Comms Adapter — cross-channel message store (comms.db).

Maps between the ontology's Message and Conversation types and
the comms.db SQLite storage. Uses qareen.db for cross-store links
and context cards.

Tables consumed from comms.db:
  messages, conversations, message_entities

Tables consumed from qareen.db:
  links, context_cards
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..model import SearchResult
from ..types import (
    ChannelType,
    ContextCard,
    Conversation,
    Link,
    LinkType,
    Message,
    MessageDirection,
    ObjectType,
)
from .base import Adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(val: str | None) -> datetime | None:
    """Parse an ISO8601 string into a datetime, or return None."""
    if not val:
        return None
    if len(val) == 10:
        try:
            return datetime.fromisoformat(val + "T00:00:00")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return None


def _to_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO8601 string, or return None."""
    if dt is None:
        return None
    return dt.isoformat()


def _json_loads(val: str | None, default=None):
    """Safely parse a JSON string."""
    if not val:
        return default if default is not None else {}
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _json_dumps(val: dict | list | None) -> str | None:
    """Serialize to JSON string or return None."""
    if val is None:
        return None
    if isinstance(val, dict) and len(val) == 0:
        return None
    return json.dumps(val)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_channel(val: str | None) -> ChannelType:
    """Convert a string to ChannelType enum."""
    if not val:
        return ChannelType.TELEGRAM
    try:
        return ChannelType(val)
    except ValueError:
        return ChannelType.TELEGRAM


def _to_direction(val: str | None) -> MessageDirection:
    """Convert a string to MessageDirection enum."""
    if not val:
        return MessageDirection.INBOUND
    try:
        return MessageDirection(val)
    except ValueError:
        return MessageDirection.INBOUND


def _row_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    """Row factory that returns dicts."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# ---------------------------------------------------------------------------
# CommsAdapter
# ---------------------------------------------------------------------------

class CommsAdapter(Adapter):
    """Adapter for cross-channel message store (comms.db)."""

    def __init__(self, data_dir: Path):
        self._db_path = data_dir / "comms.db"
        self._conn: sqlite3.Connection | None = None  # lazy connection
        self._qareen_path = data_dir / "qareen.db"  # for links
        self._qareen_conn: sqlite3.Connection | None = None

    # -- connection helpers --------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection | None:
        """Lazy-open comms.db. Returns None if DB does not exist."""
        if self._conn is not None:
            return self._conn
        if not self._db_path.exists():
            return None
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = _row_dict
        return self._conn

    def _get_qareen_conn(self) -> sqlite3.Connection | None:
        """Lazy-open qareen.db. Returns None if DB does not exist."""
        if self._qareen_conn is not None:
            return self._qareen_conn
        if not self._qareen_path.exists():
            return None
        self._qareen_conn = sqlite3.connect(str(self._qareen_path))
        self._qareen_conn.execute("PRAGMA journal_mode=WAL")
        self._qareen_conn.row_factory = _row_dict
        return self._qareen_conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._qareen_conn is not None:
            self._qareen_conn.close()
            self._qareen_conn = None

    # -- Adapter property ----------------------------------------------------

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.MESSAGE

    # -- Core CRUD -----------------------------------------------------------

    def get(self, object_id: str) -> Message | Conversation | None:
        """Get a single object by id. Tries messages first, then conversations."""
        conn = self._get_conn()
        if conn is None:
            return None

        # Try messages first (most common lookup)
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (object_id,)
        ).fetchone()
        if row is not None:
            return self._row_to_message(row)

        # Try conversations
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (object_id,)
        ).fetchone()
        if row is not None:
            return self._row_to_conversation(row)

        return None

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List objects with optional filters.

        Special filter keys:
          _type: 'message' | 'conversation' (default: 'message')

        Message filters:
          channel, direction, person_id, conversation_id,
          processed, intent, urgency, date_from, date_to

        Conversation filters:
          channel, person_id, status
        """
        conn = self._get_conn()
        if conn is None:
            return []

        filters = dict(filters) if filters else {}
        obj_type = filters.pop("_type", "message")

        if obj_type == "conversation":
            return self._list_conversations(conn, filters, limit, offset)
        else:
            return self._list_messages(conn, filters, limit, offset)

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        """Count objects matching filters."""
        conn = self._get_conn()
        if conn is None:
            return 0

        filters = dict(filters) if filters else {}
        obj_type = filters.pop("_type", "message")

        if obj_type == "conversation":
            return self._count_table(conn, "conversations", filters)
        else:
            return self._count_table(conn, "messages", filters)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """LIKE search on messages.content. Returns SearchResult objects."""
        conn = self._get_conn()
        if conn is None:
            return []

        rows = conn.execute(
            "SELECT * FROM messages "
            "WHERE content LIKE ? "
            "ORDER BY timestamp DESC "
            "LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()

        results: list[SearchResult] = []
        query_lower = query.lower()
        for row in rows:
            msg = self._row_to_message(row)
            content_lower = (msg.content or "").lower()

            # Simple scoring
            if query_lower == content_lower:
                score = 1.0
            elif content_lower.startswith(query_lower):
                score = 0.8
            else:
                score = 0.5

            # Build a snippet — first 120 chars of content
            snippet = (msg.content or "")[:120]

            results.append(SearchResult(
                object_type=ObjectType.MESSAGE,
                object_id=msg.id,
                title=f"{msg.channel.value} {msg.direction.value} — {msg.timestamp.isoformat()[:16]}",
                snippet=snippet,
                score=score,
                obj=msg,
            ))
        return results

    def create(self, obj: Any) -> Any:
        """Insert into messages or conversations table."""
        conn = self._get_conn()
        if conn is None:
            # DB doesn't exist yet — can't create without schema
            raise FileNotFoundError(
                f"comms.db not found at {self._db_path}. "
                "Run schema migration first."
            )

        if isinstance(obj, Message):
            return self._create_message(conn, obj)
        elif isinstance(obj, Conversation):
            return self._create_conversation(conn, obj)
        else:
            raise TypeError(f"Unsupported type: {type(obj)}")

    def update(self, object_id: str, fields: dict[str, Any]) -> Any | None:
        """Update messages or conversations."""
        conn = self._get_conn()
        if conn is None:
            return None

        # Determine which table this ID belongs to
        row = conn.execute(
            "SELECT id FROM messages WHERE id = ?", (object_id,)
        ).fetchone()
        if row:
            return self._update_message(conn, object_id, fields)

        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (object_id,)
        ).fetchone()
        if row:
            return self._update_conversation(conn, object_id, fields)

        return None

    def delete(self, object_id: str) -> bool:
        """No deletion — same pattern as people. Messages are immutable."""
        return False

    # -- Relationship methods ------------------------------------------------

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get IDs of linked objects from qareen.db links + message_entities."""
        ids: list[str] = []

        # 1. Explicit links in qareen.db
        qareen = self._get_qareen_conn()
        if qareen is not None:
            sql = """
                SELECT to_id FROM links
                WHERE from_id = ? AND to_type = ?
            """
            params: list[Any] = [obj_id, target_type.value]
            if link_type is not None:
                sql += " AND link_type = ?"
                params.append(link_type.value)
            sql += " LIMIT ?"
            params.append(limit)

            for row in qareen.execute(sql, params).fetchall():
                ids.append(row["to_id"])

            # Also check reverse direction
            sql_rev = """
                SELECT from_id FROM links
                WHERE to_id = ? AND from_type = ?
            """
            params_rev: list[Any] = [obj_id, target_type.value]
            if link_type is not None:
                sql_rev += " AND link_type = ?"
                params_rev.append(link_type.value)
            sql_rev += " LIMIT ?"
            params_rev.append(limit)

            for row in qareen.execute(sql_rev, params_rev).fetchall():
                if row["from_id"] not in ids:
                    ids.append(row["from_id"])

        # 2. Implicit links from message_entities table
        conn = self._get_conn()
        if conn is not None:
            # Map target_type to entity_type strings used in message_entities
            entity_type_map = {
                ObjectType.PERSON: "person",
                ObjectType.PROJECT: "project",
                ObjectType.TASK: "task",
            }
            entity_type = entity_type_map.get(target_type)
            if entity_type:
                entity_rows = conn.execute(
                    "SELECT entity_id FROM message_entities "
                    "WHERE message_id = ? AND entity_type = ? "
                    "LIMIT ?",
                    (obj_id, entity_type, limit),
                ).fetchall()
                for row in entity_rows:
                    eid = row["entity_id"]
                    if eid not in ids:
                        ids.append(eid)

        return ids[:limit]

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Insert a link into qareen.db's links table."""
        qareen = self._get_qareen_conn()
        if qareen is None:
            raise FileNotFoundError(
                f"qareen.db not found at {self._qareen_path}. "
                "Run schema migration first."
            )

        now = datetime.now().isoformat()
        link_id = f"lnk_{uuid.uuid4().hex[:12]}"
        props = json.dumps(metadata) if metadata else None

        # Detect source type — could be MESSAGE or CONVERSATION
        source_type = self._detect_type(source_id)

        qareen.execute(
            """INSERT INTO links
               (id, link_type, from_type, from_id, to_type, to_id,
                direction, properties, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, 'directed', ?, ?, 'comms_adapter')""",
            (
                link_id,
                link_type.value,
                source_type.value,
                source_id,
                target_type.value,
                target_id,
                props,
                now,
            ),
        )
        qareen.commit()

        return Link(
            link_type=link_type,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
        )

    # -- Context cards -------------------------------------------------------

    def get_context_card(self, object_id: str) -> ContextCard | None:
        """Read from qareen.db context_cards."""
        qareen = self._get_qareen_conn()
        if qareen is None:
            return None

        # Try message first, then conversation
        entity_type = self._detect_type(object_id)

        row = qareen.execute(
            """SELECT * FROM context_cards
               WHERE entity_type = ? AND entity_id = ?""",
            (entity_type.value, object_id),
        ).fetchone()
        if row is None:
            return None

        return ContextCard(
            entity_type=entity_type,
            entity_id=object_id,
            summary=row["summary"],
            key_facts=json.loads(row["key_facts"]) if row.get("key_facts") else [],
            recent_activity=json.loads(row["recent_activity"])
            if row.get("recent_activity")
            else [],
            open_items=json.loads(row["open_items"]) if row.get("open_items") else [],
            built_at=datetime.fromisoformat(row["built_at"])
            if row.get("built_at")
            else datetime.now(),
            stale_after=datetime.fromisoformat(row["stale_after"])
            if row.get("stale_after")
            else None,
        )

    # -----------------------------------------------------------------------
    # Internal: Row-to-dataclass mappers
    # -----------------------------------------------------------------------

    def _row_to_message(self, row: dict) -> Message:
        """Convert a messages table row to a Message dataclass."""
        return Message(
            id=row["id"],
            channel=_to_channel(row.get("channel")),
            direction=_to_direction(row.get("direction")),
            sender_id=row.get("sender_id"),
            recipient_id=row.get("recipient_id"),
            content=row.get("content") or "",
            timestamp=_parse_dt(row.get("timestamp")) or datetime.now(),
            thread_id=row.get("thread_id"),
            reply_to_id=row.get("reply_to_id"),
            has_attachment=bool(row.get("has_attachment", 0)),
            attachment_type=row.get("attachment_type"),
            attachment_path=row.get("attachment_path"),
            processed=bool(row.get("processed", 0)),
            channel_metadata=_json_loads(row.get("channel_metadata")),
            person_id=row.get("person_id"),
            conversation_id=row.get("conversation_id"),
            intent=row.get("intent"),
            urgency=row.get("urgency") or 0,
        )

    def _row_to_conversation(self, row: dict) -> Conversation:
        """Convert a conversations table row to a Conversation dataclass."""
        return Conversation(
            id=row["id"],
            channel=_to_channel(row.get("channel")),
            person_id=row.get("person_id"),
            name=row.get("name") or "",
            status=row.get("status") or "open",
            last_message_at=_parse_dt(row.get("last_message_at")),
            message_count=row.get("message_count") or 0,
            unread_count=row.get("unread_count") or 0,
            metadata=_json_loads(row.get("metadata")),
        )

    # -----------------------------------------------------------------------
    # Internal: Message operations
    # -----------------------------------------------------------------------

    def _list_messages(
        self, conn: sqlite3.Connection, filters: dict, limit: int, offset: int
    ) -> list[Message]:
        clauses: list[str] = []
        params: list[Any] = []

        if "channel" in filters:
            clauses.append("channel = ?")
            params.append(filters["channel"])
        if "direction" in filters:
            clauses.append("direction = ?")
            params.append(filters["direction"])
        if "person_id" in filters:
            clauses.append("person_id = ?")
            params.append(filters["person_id"])
        if "conversation_id" in filters:
            clauses.append("conversation_id = ?")
            params.append(filters["conversation_id"])
        if "processed" in filters:
            clauses.append("processed = ?")
            params.append(int(filters["processed"]))
        if "intent" in filters:
            clauses.append("intent = ?")
            params.append(filters["intent"])
        if "urgency" in filters:
            clauses.append("urgency = ?")
            params.append(int(filters["urgency"]))
        if "date_from" in filters:
            clauses.append("timestamp >= ?")
            params.append(filters["date_from"])
        if "date_to" in filters:
            clauses.append("timestamp <= ?")
            params.append(filters["date_to"])

        where = " AND ".join(clauses) if clauses else "1=1"
        query = (
            f"SELECT * FROM messages WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [self._row_to_message(r) for r in rows]

    def _list_conversations(
        self, conn: sqlite3.Connection, filters: dict, limit: int, offset: int
    ) -> list[Conversation]:
        clauses: list[str] = []
        params: list[Any] = []

        if "channel" in filters:
            clauses.append("channel = ?")
            params.append(filters["channel"])
        if "person_id" in filters:
            clauses.append("person_id = ?")
            params.append(filters["person_id"])
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])

        where = " AND ".join(clauses) if clauses else "1=1"
        query = (
            f"SELECT * FROM conversations WHERE {where} "
            f"ORDER BY last_message_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def _count_table(
        self, conn: sqlite3.Connection, table: str, filters: dict
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []

        if "channel" in filters:
            clauses.append("channel = ?")
            params.append(filters["channel"])

        if table == "messages":
            if "direction" in filters:
                clauses.append("direction = ?")
                params.append(filters["direction"])
            if "person_id" in filters:
                clauses.append("person_id = ?")
                params.append(filters["person_id"])
            if "conversation_id" in filters:
                clauses.append("conversation_id = ?")
                params.append(filters["conversation_id"])
            if "processed" in filters:
                clauses.append("processed = ?")
                params.append(int(filters["processed"]))
            if "intent" in filters:
                clauses.append("intent = ?")
                params.append(filters["intent"])
            if "urgency" in filters:
                clauses.append("urgency = ?")
                params.append(int(filters["urgency"]))
            if "date_from" in filters:
                clauses.append("timestamp >= ?")
                params.append(filters["date_from"])
            if "date_to" in filters:
                clauses.append("timestamp <= ?")
                params.append(filters["date_to"])
        elif table == "conversations":
            if "person_id" in filters:
                clauses.append("person_id = ?")
                params.append(filters["person_id"])
            if "status" in filters:
                clauses.append("status = ?")
                params.append(filters["status"])

        where = " AND ".join(clauses) if clauses else "1=1"
        row = conn.execute(
            f"SELECT count(*) as cnt FROM {table} WHERE {where}", params
        ).fetchone()
        return row["cnt"] if row else 0

    # -----------------------------------------------------------------------
    # Internal: Create operations
    # -----------------------------------------------------------------------

    def _create_message(self, conn: sqlite3.Connection, msg: Message) -> Message:
        conn.execute(
            "INSERT OR REPLACE INTO messages "
            "(id, channel, direction, sender_id, recipient_id, content, "
            " timestamp, thread_id, reply_to_id, has_attachment, "
            " attachment_type, attachment_path, processed, channel_metadata, "
            " person_id, conversation_id, intent, urgency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg.id,
                msg.channel.value,
                msg.direction.value,
                msg.sender_id,
                msg.recipient_id,
                msg.content,
                _to_iso(msg.timestamp) or _now(),
                msg.thread_id,
                msg.reply_to_id,
                int(msg.has_attachment),
                msg.attachment_type,
                msg.attachment_path,
                int(msg.processed),
                _json_dumps(msg.channel_metadata),
                msg.person_id,
                msg.conversation_id,
                msg.intent,
                msg.urgency,
            ),
        )
        conn.commit()
        return msg

    def _create_conversation(
        self, conn: sqlite3.Connection, conv: Conversation
    ) -> Conversation:
        conn.execute(
            "INSERT OR REPLACE INTO conversations "
            "(id, channel, person_id, name, status, last_message_at, "
            " message_count, unread_count, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conv.id,
                conv.channel.value,
                conv.person_id,
                conv.name,
                conv.status,
                _to_iso(conv.last_message_at),
                conv.message_count,
                conv.unread_count,
                _json_dumps(conv.metadata),
            ),
        )
        conn.commit()
        return conv

    # -----------------------------------------------------------------------
    # Internal: Update operations
    # -----------------------------------------------------------------------

    def _update_message(
        self, conn: sqlite3.Connection, msg_id: str, fields: dict
    ) -> Message | None:
        sets: list[str] = []
        params: list[Any] = []

        field_map = {
            "content": "content",
            "processed": "processed",
            "person_id": "person_id",
            "conversation_id": "conversation_id",
            "intent": "intent",
            "urgency": "urgency",
            "sender_id": "sender_id",
            "recipient_id": "recipient_id",
            "thread_id": "thread_id",
            "reply_to_id": "reply_to_id",
            "has_attachment": "has_attachment",
            "attachment_type": "attachment_type",
            "attachment_path": "attachment_path",
        }

        for key, val in fields.items():
            if key in field_map:
                col = field_map[key]
                if col == "processed":
                    sets.append(f"{col} = ?")
                    params.append(int(val) if not isinstance(val, int) else val)
                elif col == "has_attachment":
                    sets.append(f"{col} = ?")
                    params.append(int(val) if not isinstance(val, int) else val)
                elif col == "urgency":
                    sets.append(f"{col} = ?")
                    params.append(int(val) if not isinstance(val, int) else val)
                else:
                    sets.append(f"{col} = ?")
                    params.append(val)
            elif key == "channel_metadata":
                sets.append("channel_metadata = ?")
                params.append(_json_dumps(val))

        if not sets:
            return self.get(msg_id)

        params.append(msg_id)
        conn.execute(
            f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        return self._row_to_message(row) if row else None

    def _update_conversation(
        self, conn: sqlite3.Connection, conv_id: str, fields: dict
    ) -> Conversation | None:
        sets: list[str] = []
        params: list[Any] = []

        for key, val in fields.items():
            if key in ("name", "status", "person_id"):
                sets.append(f"{key} = ?")
                params.append(val)
            elif key == "last_message_at":
                sets.append("last_message_at = ?")
                params.append(
                    _to_iso(val) if isinstance(val, datetime) else val
                )
            elif key == "message_count":
                sets.append("message_count = ?")
                params.append(int(val))
            elif key == "unread_count":
                sets.append("unread_count = ?")
                params.append(int(val))
            elif key == "metadata":
                sets.append("metadata = ?")
                params.append(_json_dumps(val))

        if not sets:
            return self.get(conv_id)

        params.append(conv_id)
        conn.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        return self._row_to_conversation(row) if row else None

    # -----------------------------------------------------------------------
    # Internal: Helpers
    # -----------------------------------------------------------------------

    def _detect_type(self, object_id: str) -> ObjectType:
        """Detect whether an object ID is a message or conversation."""
        conn = self._get_conn()
        if conn is not None:
            if conn.execute(
                "SELECT 1 FROM messages WHERE id = ?", (object_id,)
            ).fetchone():
                return ObjectType.MESSAGE
            if conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (object_id,)
            ).fetchone():
                return ObjectType.CONVERSATION
        return ObjectType.MESSAGE  # default
