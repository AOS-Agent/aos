"""Chat API — thin proxy to the Bridge service.

The Bridge owns the persistent Claude CLI session and runs on port 4098.
This module exposes /api/chat/* endpoints on Qareen so the frontend only
ever talks to one backend (port 4096). Qareen forwards chat requests to
Bridge internally.

This is Phase 1 of the chat + companion merge — unified API surface.
The frontend SSE connection for chat events is handled by the existing
companion stream; Bridge events are rebroadcast as `chat.*` by
`qareen.channels.bridge_listener`.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

BRIDGE_URL = "http://127.0.0.1:4098"
SEND_TIMEOUT = httpx.Timeout(120.0)
QUICK_TIMEOUT = httpx.Timeout(10.0)


class ChatSendRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Message text")
    source: str = Field("centcom", description="Originating surface")


@router.post("/send")
async def send(req: ChatSendRequest) -> dict:
    """Forward a chat message to the Bridge persistent session.

    Bridge runs the Claude CLI session, emits events via its own SSE,
    and returns a final result. The companion SSE carries the streamed
    events to the frontend (via bridge_listener rebroadcast).
    """
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            r = await client.post(f"{BRIDGE_URL}/send", json=req.model_dump())
    except httpx.HTTPError as e:
        logger.warning("Bridge send failed: %s", e)
        raise HTTPException(status_code=503, detail=f"Bridge unavailable: {e}") from e

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/history")
async def history() -> list[dict]:
    """Return the current conversation's history in Bridge event shape.

    Phase 2: reads from ConversationStore (SQLite) instead of proxying
    Bridge's in-memory ring buffer. Messages are shaped to match the
    legacy Bridge event schema so the frontend doesn't need to change.
    """
    from qareen.api.conversations import get_store

    try:
        store = get_store()
        convo = store.get_current()
        if not convo:
            return []
        messages = store.get_messages(convo.id, limit=500)
    except Exception as e:
        logger.debug("Conversation history fetch failed: %s", e)
        return []

    # Shape to the Bridge event format the frontend already understands.
    events: list[dict] = []
    for m in messages:
        if m.role == "user":
            events.append({
                "ts": m.ts,
                "type": "user_message",
                "text": m.text,
                "source": m.source or "centcom",
            })
        elif m.role == "assistant":
            # Emit a text_complete + result pair so the frontend's
            # legacy history reconstruction path continues to work.
            events.append({
                "ts": m.ts,
                "type": "text_complete",
                "text": m.text,
            })
            events.append({
                "ts": m.ts,
                "type": "result",
                "text": m.text,
                "is_error": False,
                "duration_ms": m.duration_ms,
                "cost_usd": m.cost_usd,
            })
        elif m.role == "tool":
            events.append({
                "ts": m.ts,
                "type": "tool_start",
                "name": m.tool_name,
                "preview": m.tool_preview or m.text,
            })
    return events


@router.post("/cancel")
async def cancel() -> dict:
    """Cancel the Bridge's currently in-flight generation."""
    try:
        async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
            r = await client.post(f"{BRIDGE_URL}/cancel")
    except httpx.HTTPError as e:
        logger.warning("Bridge cancel failed: %s", e)
        raise HTTPException(status_code=503, detail=f"Bridge unavailable: {e}") from e

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/health")
async def health() -> dict:
    """Check if Bridge is reachable."""
    try:
        async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
            r = await client.get(f"{BRIDGE_URL}/health")
            if r.status_code == 200:
                data = r.json()
                return {"ok": True, "bridge": data}
    except httpx.HTTPError:
        pass
    return {"ok": False, "bridge": None}
