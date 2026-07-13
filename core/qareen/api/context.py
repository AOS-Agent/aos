"""Qareen API -- Context Store routes.

Exposes the persistent qareen context for all surfaces (Companion,
Quick Assist, Chat) to read and write shared state.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["context"])


def _get_store(request: Request):
    """Retrieve the QareenContextStore from app state."""
    return request.app.state.context_store


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/api/context")
async def get_context(request: Request) -> dict:
    """Returns the full current qareen context."""
    store = _get_store(request)
    return asdict(store.get())


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@router.patch("/api/context")
async def update_context(request: Request) -> dict:
    """Update specific context fields. Body is a flat JSON object of fields to set."""
    store = _get_store(request)
    body = await request.json()
    return asdict(store.update(**body))


@router.post("/api/context/action")
async def log_action(request: Request) -> dict:
    """Log a quick-assist action.

    Body: {"input": str, "action_id": str, "spoken": str, "page": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.add_action(body)
    return {"ok": True}


@router.post("/api/context/entity")
async def log_entity(request: Request) -> dict:
    """Log an entity mention.

    Body: {"name": str, "type": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.add_entity(body)
    return {"ok": True}


@router.post("/api/context/decision")
async def log_decision(request: Request) -> dict:
    """Log a decision.

    Body: {"text": str, "thread": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.add_decision(body)
    return {"ok": True}


@router.post("/api/context/focus")
async def set_focus(request: Request) -> dict:
    """Set or clear the current work focus.

    Body: {"focus": str | null}
    """
    store = _get_store(request)
    body = await request.json()
    store.set_focus(body.get("focus"))
    return {"ok": True}


@router.post("/api/context/page")
async def log_page(request: Request) -> dict:
    """Log a page navigation.

    Body: {"page": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.add_page(body.get("page", ""))
    return {"ok": True}


@router.post("/api/context/approval")
async def log_approval(request: Request) -> dict:
    """Record an approval -- lowers the classification threshold.

    Body: {"classification": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.record_approval(body.get("classification", ""))
    return {"ok": True}


@router.post("/api/context/dismissal")
async def log_dismissal(request: Request) -> dict:
    """Record a dismissal -- raises the classification threshold.

    Body: {"classification": str}
    """
    store = _get_store(request)
    body = await request.json()
    store.record_dismissal(body.get("classification", ""))
    return {"ok": True}
