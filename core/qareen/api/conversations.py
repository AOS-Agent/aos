"""Conversations API — the unified conversation surface.

Phase 2 of the chat + companion merge. Every chat and every companion
"session" is a Conversation. Capabilities control the depth: simple
chat enables only `response`, a planning session enables everything.

Routes:
    GET    /api/conversations               list (current + archived)
    POST   /api/conversations               create
    GET    /api/conversations/current       fetch the active one (auto-create)
    GET    /api/conversations/{id}          fetch one (with messages)
    PATCH  /api/conversations/{id}          rename, toggle capabilities, metadata
    DELETE /api/conversations/{id}          hard delete
    POST   /api/conversations/{id}/archive  archive (keeps history)
    POST   /api/conversations/{id}/activate set this one as current
    POST   /api/conversations/{id}/messages append a message (used by
                                            frontend or migration flows)
    GET    /api/conversations/{id}/messages list messages
    POST   /api/conversations/import        bulk ingest (localStorage
                                            migration endpoint)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from qareen.conversations import ConversationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# Module-level store — safe because ConversationStore uses per-call connections
_store: ConversationStore | None = None


def get_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
        # Best-effort migration of legacy companion_sessions on first access
        try:
            _store.migrate_companion_sessions()
        except Exception:
            logger.exception("Legacy companion_sessions migration failed (non-fatal)")
    return _store


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class CreateConversationReq(BaseModel):
    title: str | None = None
    capabilities: dict[str, bool] | None = None
    metadata: dict[str, Any] | None = None
    make_current: bool = True


class UpdateConversationReq(BaseModel):
    title: str | None = None
    capabilities: dict[str, bool] | None = None
    metadata: dict[str, Any] | None = None


class AppendMessageReq(BaseModel):
    role: str = Field(..., description="user | assistant | tool | system")
    text: str = Field(..., min_length=1)
    speaker: str | None = None
    source: str | None = None
    tool_name: str | None = None
    tool_preview: str | None = None
    is_error: bool = False
    duration_ms: int | None = None
    cost_usd: float | None = None
    ts: float | None = None
    metadata: dict[str, Any] | None = None


class ImportMessageDTO(BaseModel):
    role: str
    text: str
    ts: float | None = None
    source: str | None = None
    speaker: str | None = None
    tool_name: str | None = None
    tool_preview: str | None = None
    is_error: bool = False
    duration_ms: int | None = None
    cost_usd: float | None = None


class ImportConversationDTO(BaseModel):
    title: str | None = None
    created_at: str | None = None
    capabilities: dict[str, bool] | None = None
    metadata: dict[str, Any] | None = None
    archived: bool = True
    messages: list[ImportMessageDTO] = Field(default_factory=list)


class ImportRequest(BaseModel):
    conversations: list[ImportConversationDTO]


# ---------------------------------------------------------------------------
# Routes — conversations
# ---------------------------------------------------------------------------


@router.get("")
async def list_conversations(
    include_archived: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    store = get_store()
    rows = store.list(include_archived=include_archived, limit=limit, offset=offset)
    return [_shape_conversation_summary(c, store) for c in rows]


@router.post("")
async def create_conversation(req: CreateConversationReq) -> dict:
    store = get_store()
    c = store.create(
        title=req.title,
        capabilities=req.capabilities,
        metadata=req.metadata,
        make_current=req.make_current,
    )
    return c.to_dict()


@router.get("/current")
async def get_current_conversation() -> dict:
    store = get_store()
    c = store.get_or_create_current()
    return _shape_conversation_full(c, store)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    store = get_store()
    c = store.get(conversation_id)
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _shape_conversation_full(c, store)


@router.patch("/{conversation_id}")
async def update_conversation(conversation_id: str, req: UpdateConversationReq) -> dict:
    store = get_store()
    c = store.update(
        conversation_id,
        title=req.title,
        capabilities=req.capabilities,
        metadata=req.metadata,
    )
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c.to_dict()


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    store = get_store()
    ok = store.delete(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.post("/{conversation_id}/archive")
async def archive_conversation(conversation_id: str) -> dict:
    store = get_store()
    c = store.archive(conversation_id)
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c.to_dict()


@router.post("/{conversation_id}/activate")
async def activate_conversation(conversation_id: str) -> dict:
    store = get_store()
    c = store.set_current(conversation_id)
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c.to_dict()


# ---------------------------------------------------------------------------
# Routes — messages
# ---------------------------------------------------------------------------


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    store = get_store()
    if not store.get(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = store.get_messages(conversation_id, limit=limit, offset=offset)
    return [m.to_dict() for m in msgs]


@router.post("/{conversation_id}/messages")
async def append_message(conversation_id: str, req: AppendMessageReq) -> dict:
    store = get_store()
    if not store.get(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = store.append_message(
        conversation_id,
        role=req.role,
        text=req.text,
        speaker=req.speaker,
        source=req.source,
        tool_name=req.tool_name,
        tool_preview=req.tool_preview,
        is_error=req.is_error,
        duration_ms=req.duration_ms,
        cost_usd=req.cost_usd,
        ts=req.ts,
        metadata=req.metadata,
    )
    return msg.to_dict()


# ---------------------------------------------------------------------------
# Bulk import (localStorage migration)
# ---------------------------------------------------------------------------


@router.post("/import")
async def import_conversations(req: ImportRequest) -> dict:
    """Bulk import conversations + messages.

    Used by the frontend on first load to migrate legacy chat history
    from localStorage into SQLite. Idempotency is the caller's
    responsibility (store a flag in localStorage to avoid re-importing).
    """
    store = get_store()
    imported = 0
    for dto in req.conversations:
        convo = store.create(
            title=dto.title,
            capabilities=dto.capabilities,
            metadata=dto.metadata or {},
            make_current=False,
        )
        for msg in dto.messages:
            store.append_message(
                convo.id,
                role=msg.role,
                text=msg.text,
                ts=msg.ts,
                source=msg.source,
                speaker=msg.speaker,
                tool_name=msg.tool_name,
                tool_preview=msg.tool_preview,
                is_error=msg.is_error,
                duration_ms=msg.duration_ms,
                cost_usd=msg.cost_usd,
            )
        if dto.archived:
            store.archive(convo.id)
        imported += 1
    return {"imported": imported}


# ---------------------------------------------------------------------------
# Shape helpers
# ---------------------------------------------------------------------------


def _shape_conversation_summary(c, store: ConversationStore) -> dict:
    d = c.to_dict()
    d["message_count"] = store.count_messages(c.id)
    return d


def _shape_conversation_full(c, store: ConversationStore) -> dict:
    d = c.to_dict()
    msgs = store.get_messages(c.id)
    d["messages"] = [m.to_dict() for m in msgs]
    d["message_count"] = len(msgs)
    return d
