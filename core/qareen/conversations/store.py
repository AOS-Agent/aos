"""ConversationStore — SQLite persistence for unified conversations.

Schema (tables created on first init):

    conversations(
        id              TEXT PRIMARY KEY,
        title           TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        is_current      INTEGER DEFAULT 0,  -- 0/1 — only one row can be 1
        capabilities    TEXT NOT NULL,      -- JSON: {"response":true,"voice":false,...}
        archived_at     TEXT,               -- NULL if active
        metadata        TEXT DEFAULT '{}'   -- JSON: legacy fields, session skill, etc.
    )

    conversation_messages(
        id               TEXT PRIMARY KEY,
        conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role             TEXT NOT NULL,   -- user | assistant | tool | system
        text             TEXT NOT NULL,
        speaker          TEXT,            -- "You", "Claude", etc.
        ts               REAL NOT NULL,   -- unix timestamp (float)
        source           TEXT,            -- chat | voice | telegram | centcom
        tool_name        TEXT,
        tool_preview     TEXT,
        is_error         INTEGER DEFAULT 0,
        duration_ms      INTEGER,
        cost_usd         REAL,
        metadata         TEXT DEFAULT '{}'  -- JSON: anything extra
    )

The default db path is ~/.aos/data/qareen.db (same file the legacy
SessionManager uses — we just add new tables there, no collision).

Capabilities are a flat dict of booleans. Response is always True.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

ALL_CAPABILITIES: tuple[str, ...] = (
    "response",   # Always on — Chief replies
    "voice",      # Web Speech / Whisper voice input
    "notes",      # Notes extraction sidecar
    "approvals",  # Action card generation sidecar
    "research",   # Entity research sidecar
    "threads",    # Thread tracking sidecar
)

DEFAULT_CAPABILITIES: dict[str, bool] = {
    "response": True,
    "voice": False,
    "notes": False,
    "approvals": False,
    "research": False,
    "threads": False,
}


def normalize_capabilities(caps: dict[str, Any] | None) -> dict[str, bool]:
    """Coerce any input to a clean capability dict. Response is forced True."""
    out = dict(DEFAULT_CAPABILITIES)
    if not caps:
        return out
    for key in ALL_CAPABILITIES:
        if key in caps:
            out[key] = bool(caps[key])
    out["response"] = True  # Always on
    return out


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Conversation:
    id: str
    title: str | None
    created_at: str
    updated_at: str
    is_current: bool
    capabilities: dict[str, bool]
    archived_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_current": self.is_current,
            "capabilities": self.capabilities,
            "archived_at": self.archived_at,
            "metadata": self.metadata,
        }


@dataclass
class ConversationMessage:
    id: str
    conversation_id: str
    role: str
    text: str
    ts: float
    speaker: str | None = None
    source: str | None = None
    tool_name: str | None = None
    tool_preview: str | None = None
    is_error: bool = False
    duration_ms: int | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "text": self.text,
            "speaker": self.speaker,
            "ts": self.ts,
            "source": self.source,
            "tool_name": self.tool_name,
            "tool_preview": self.tool_preview,
            "is_error": self.is_error,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"


class ConversationStore:
    """SQLite-backed store for conversations and their messages.

    Thread-safety: uses short-lived connections per call. Safe to call
    from multiple async tasks. Not optimized for high write throughput —
    acceptable for conversation scale (~10s of messages per minute).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # -- Schema ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id              TEXT PRIMARY KEY,
                    title           TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    is_current      INTEGER DEFAULT 0,
                    capabilities    TEXT NOT NULL,
                    archived_at     TEXT,
                    metadata        TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_current
                    ON conversations(is_current);
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_archived
                    ON conversations(archived_at);

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id               TEXT PRIMARY KEY,
                    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role             TEXT NOT NULL,
                    text             TEXT NOT NULL,
                    speaker          TEXT,
                    ts               REAL NOT NULL,
                    source           TEXT,
                    tool_name        TEXT,
                    tool_preview     TEXT,
                    is_error         INTEGER DEFAULT 0,
                    duration_ms      INTEGER,
                    cost_usd         REAL,
                    metadata         TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON conversation_messages(conversation_id, ts);
                CREATE INDEX IF NOT EXISTS idx_messages_ts
                    ON conversation_messages(ts DESC);
                """
            )
            conn.commit()

    # -- Row → Conversation -----------------------------------------------

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_current=bool(row["is_current"]),
            capabilities=json.loads(row["capabilities"]),
            archived_at=row["archived_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ConversationMessage:
        return ConversationMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            text=row["text"],
            ts=row["ts"],
            speaker=row["speaker"],
            source=row["source"],
            tool_name=row["tool_name"],
            tool_preview=row["tool_preview"],
            is_error=bool(row["is_error"]),
            duration_ms=row["duration_ms"],
            cost_usd=row["cost_usd"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # -- Conversation CRUD -------------------------------------------------

    def create(
        self,
        title: str | None = None,
        capabilities: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        make_current: bool = True,
    ) -> Conversation:
        """Create a new conversation. If make_current, unset any other current."""
        now = _iso_now()
        convo = Conversation(
            id=str(uuid.uuid4()),
            title=title,
            created_at=now,
            updated_at=now,
            is_current=make_current,
            capabilities=normalize_capabilities(capabilities),
            archived_at=None,
            metadata=metadata or {},
        )
        with self._connect() as conn:
            if make_current:
                conn.execute("UPDATE conversations SET is_current = 0 WHERE is_current = 1")
            conn.execute(
                """INSERT INTO conversations
                   (id, title, created_at, updated_at, is_current, capabilities, archived_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    convo.id,
                    convo.title,
                    convo.created_at,
                    convo.updated_at,
                    int(convo.is_current),
                    json.dumps(convo.capabilities),
                    convo.archived_at,
                    json.dumps(convo.metadata),
                ),
            )
            conn.commit()
        logger.info("Conversation created: %s (current=%s)", convo.id, convo.is_current)
        return convo

    def get(self, conversation_id: str) -> Conversation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def get_current(self) -> Conversation | None:
        """Return the single current conversation, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE is_current = 1 LIMIT 1"
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def get_or_create_current(self) -> Conversation:
        """Fetch the current conversation, or create a fresh one if none exists."""
        existing = self.get_current()
        if existing:
            return existing
        return self.create(title=None)

    def set_current(self, conversation_id: str) -> Conversation | None:
        """Mark a specific conversation as current, unset others."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE conversations SET is_current = 0 WHERE is_current = 1")
            conn.execute(
                "UPDATE conversations SET is_current = 1, archived_at = NULL, updated_at = ? WHERE id = ?",
                (_iso_now(), conversation_id),
            )
            conn.commit()
        return self.get(conversation_id)

    def list(
        self,
        *,
        include_archived: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """List conversations ordered by updated_at desc."""
        sql = "SELECT * FROM conversations"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (limit, offset)).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        capabilities: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation | None:
        """Partial update — only provided fields are changed."""
        existing = self.get(conversation_id)
        if not existing:
            return None
        if title is not None:
            existing.title = title
        if capabilities is not None:
            existing.capabilities = normalize_capabilities(
                {**existing.capabilities, **capabilities}
            )
        if metadata is not None:
            existing.metadata = {**existing.metadata, **metadata}
        existing.updated_at = _iso_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE conversations
                   SET title = ?, capabilities = ?, metadata = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    existing.title,
                    json.dumps(existing.capabilities),
                    json.dumps(existing.metadata),
                    existing.updated_at,
                    existing.id,
                ),
            )
            conn.commit()
        return existing

    def archive(self, conversation_id: str) -> Conversation | None:
        """Archive a conversation (sets archived_at, unsets is_current)."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE conversations
                   SET archived_at = ?, is_current = 0, updated_at = ?
                   WHERE id = ?""",
                (_iso_now(), _iso_now(), conversation_id),
            )
            conn.commit()
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    # -- Messages ---------------------------------------------------------

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        *,
        speaker: str | None = None,
        source: str | None = None,
        tool_name: str | None = None,
        tool_preview: str | None = None,
        is_error: bool = False,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        ts: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        msg = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            text=text,
            speaker=speaker,
            ts=ts if ts is not None else time.time(),
            source=source,
            tool_name=tool_name,
            tool_preview=tool_preview,
            is_error=is_error,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            metadata=metadata or {},
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO conversation_messages
                   (id, conversation_id, role, text, speaker, ts, source,
                    tool_name, tool_preview, is_error, duration_ms, cost_usd, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.id,
                    msg.conversation_id,
                    msg.role,
                    msg.text,
                    msg.speaker,
                    msg.ts,
                    msg.source,
                    msg.tool_name,
                    msg.tool_preview,
                    int(msg.is_error),
                    msg.duration_ms,
                    msg.cost_usd,
                    json.dumps(msg.metadata),
                ),
            )
            # Touch conversation updated_at
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_iso_now(), conversation_id),
            )
            conn.commit()
        return msg

    def get_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[ConversationMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM conversation_messages
                   WHERE conversation_id = ?
                   ORDER BY ts ASC
                   LIMIT ? OFFSET ?""",
                (conversation_id, limit, offset),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def count_messages(self, conversation_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    # -- Migration from legacy companion_sessions -------------------------

    def migrate_companion_sessions(self) -> int:
        """Import legacy companion_sessions rows as archived Conversations.

        Safe to call multiple times — skips sessions already migrated
        (tracked via metadata.legacy_session_id).

        Returns the number of new conversations imported.
        """
        with self._connect() as conn:
            # Check if legacy table exists
            has_legacy = conn.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='companion_sessions'"""
            ).fetchone()
            if not has_legacy:
                return 0

            # Fetch legacy rows
            legacy_rows = conn.execute(
                """SELECT id, status, started_at, ended_at, title,
                          transcript_json, utterance_count
                     FROM companion_sessions
                    ORDER BY started_at DESC"""
            ).fetchall()

            # Find already-migrated IDs via metadata field
            existing = conn.execute(
                "SELECT metadata FROM conversations WHERE metadata LIKE '%legacy_session_id%'"
            ).fetchall()
            migrated_ids: set[str] = set()
            for row in existing:
                try:
                    meta = json.loads(row["metadata"] or "{}")
                    if legacy_id := meta.get("legacy_session_id"):
                        migrated_ids.add(legacy_id)
                except json.JSONDecodeError:
                    pass

        imported = 0
        for row in legacy_rows:
            if row["id"] in migrated_ids:
                continue
            convo = self.create(
                title=row["title"] or "Session",
                # Archived sessions: all capabilities were "on" in old model
                capabilities={
                    "response": True,
                    "voice": True,
                    "notes": True,
                    "approvals": True,
                    "research": True,
                    "threads": True,
                },
                metadata={
                    "legacy_session_id": row["id"],
                    "legacy_status": row["status"],
                    "legacy_utterance_count": row["utterance_count"],
                },
                make_current=False,
            )
            # Immediately archive it — these are historical
            self.archive(convo.id)

            # Import transcript blocks as messages
            try:
                transcript = json.loads(row["transcript_json"] or "[]")
                for block in transcript:
                    if not isinstance(block, dict):
                        continue
                    text = (block.get("text") or "").strip()
                    if not text:
                        continue
                    speaker = block.get("speaker", "You")
                    ts_str = block.get("timestamp") or row["started_at"]
                    ts_val = _parse_ts(ts_str)
                    self.append_message(
                        convo.id,
                        role="user" if speaker == "You" else "assistant",
                        text=text,
                        speaker=speaker,
                        source="legacy",
                        ts=ts_val,
                    )
            except (json.JSONDecodeError, TypeError):
                pass

            imported += 1

        if imported:
            logger.info("Migrated %d legacy companion sessions to conversations", imported)
        return imported


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts_str: str | None) -> float:
    """Parse an ISO timestamp to unix float. Falls back to now."""
    if not ts_str:
        return time.time()
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()
