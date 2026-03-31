"""Qareen API — Companion routes.

Handles text input from the companion screen, processes it through
the intelligence engine (Tier 0 regex classifier + card generator),
and manages card approval/dismiss lifecycle.

Endpoints:
  POST /companion/input       — Process text through intelligence engine
  POST /companion/cards/{id}/approve  — Approve a card (execute its action)
  POST /companion/cards/{id}/dismiss  — Dismiss a card
  PATCH /companion/cards/{id}         — Edit a card's fields
  POST /companion/cards/approve-batch — Batch approve multiple cards
  GET  /companion/stream      — SSE stream for companion events
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import fields as dc_fields
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..intelligence.classifier import classify
from ..intelligence.generator import generate_card
from ..intelligence.types import Card, TaskCard

logger = logging.getLogger(__name__)

router = APIRouter(tags=["companion"])

# ---------------------------------------------------------------------------
# In-memory card store — maps card_id to serialized card dict
# ---------------------------------------------------------------------------

_card_store: dict[str, dict[str, Any]] = {}

HEARTBEAT_INTERVAL = 15  # seconds


def _card_to_dict(card: Card) -> dict[str, Any]:
    """Serialize a Card dataclass to a JSON-safe dict."""
    result: dict[str, Any] = {}
    for f in dc_fields(card):
        value = getattr(card, f.name)
        if isinstance(value, datetime):
            result[f.name] = value.isoformat()
        elif hasattr(value, "value"):
            result[f.name] = value.value
        else:
            result[f.name] = value
    return result


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CompanionInput(BaseModel):
    """Text input from the companion screen."""
    text: str = Field(..., description="Operator's text input", min_length=1)
    source: str = Field("text", description="Input source: text, voice, telegram")


class CompanionResponse(BaseModel):
    """Response from processing companion input."""
    intent: str = Field(..., description="Classified intent")
    confidence: float = Field(..., description="Classification confidence")
    card_id: str | None = Field(None, description="Generated card ID, if any")
    card_type: str | None = Field(None, description="Generated card type, if any")
    card: dict[str, Any] | None = Field(None, description="Full card data, if generated")


class CardEditRequest(BaseModel):
    """Partial card update request."""
    title: str | None = None
    body: str | None = None
    task_title: str | None = None
    task_project: str | None = None
    task_priority: int | None = None


# ---------------------------------------------------------------------------
# SSE connections for companion stream
# ---------------------------------------------------------------------------

_companion_queues: dict[str, asyncio.Queue] = {}


async def _push_companion_event(event_type: str, data: dict[str, Any]) -> None:
    """Push an event to all connected companion SSE clients."""
    event_id = str(uuid.uuid4())
    payload = json.dumps(data, default=str)
    message = f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"

    dead: list[str] = []
    for conn_id, queue in _companion_queues.items():
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead.append(conn_id)

    for conn_id in dead:
        _companion_queues.pop(conn_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/companion/input", response_model=CompanionResponse)
async def companion_input(req: CompanionInput, request: Request) -> CompanionResponse:
    """Process text input through the intelligence engine.

    1. Classify intent using Tier 0 regex patterns
    2. Generate a card if the intent is actionable
    3. Store the card and push it via SSE to the companion screen
    4. Push the input text as a transcript segment
    """
    # Classify
    intent_result = classify(req.text)

    # Push transcript segment via companion SSE
    await _push_companion_event("transcript", {
        "id": str(uuid.uuid4()),
        "speaker": "You",
        "text": req.text,
        "timestamp": datetime.now().isoformat(),
        "is_provisional": False,
        "is_update": False,
    })

    # Generate card
    card = generate_card(intent_result)

    if card:
        card_dict = _card_to_dict(card)
        _card_store[card.id] = card_dict

        # Push the full card via companion SSE
        await _push_companion_event("card", card_dict)

        # Also emit via the main EventBus for system-wide awareness
        bus = getattr(request.app.state, "bus", None)
        if bus:
            from ..events.types import Event
            await bus.emit(Event(
                event_type="card.generated",
                source="companion",
                payload={
                    "card_id": card.id,
                    "card_type": card.card_type,
                    "title": card.title,
                },
            ))

        return CompanionResponse(
            intent=intent_result.intent.value,
            confidence=intent_result.confidence,
            card_id=card.id,
            card_type=card.card_type,
            card=card_dict,
        )

    return CompanionResponse(
        intent=intent_result.intent.value,
        confidence=intent_result.confidence,
    )


@router.post("/companion/cards/{card_id}/approve")
async def approve_card(card_id: str, request: Request) -> JSONResponse:
    """Approve a card — execute its associated action.

    For TaskCards, this creates the task via the action registry.
    For other card types, marks them as approved (action TBD).
    """
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    card_type = card_data.get("card_type")
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)

    result_data: dict[str, Any] = {"card_id": card_id, "status": "approved"}

    if card_type == "task" and registry and ontology:
        is_update = card_data.get("is_update", False)

        if is_update:
            # Task completion
            task_title = card_data.get("task_title", "")
            # Try to find the task by fuzzy title match
            result = await registry.execute("complete_task", {
                "ontology": ontology,
                "task_id": task_title,  # Will need fuzzy resolution
            }, actor="operator")
            result_data["action"] = "complete_task"
            result_data["result"] = result
        else:
            # Task creation
            result = await registry.execute("create_task", {
                "ontology": ontology,
                "title": card_data.get("task_title", card_data.get("title", "")),
                "project": card_data.get("task_project"),
                "priority": card_data.get("task_priority", 3),
            }, actor="operator")
            result_data["action"] = "create_task"
            result_data["result"] = result

    elif card_type == "vault" and registry and ontology:
        # Vault capture — create an inbox item for now
        result = await registry.execute("create_inbox", {
            "ontology": ontology,
            "content": card_data.get("body", ""),
            "source": "companion",
        }, actor="operator")
        result_data["action"] = "create_inbox"
        result_data["result"] = result

    # Mark card as approved
    card_data["status"] = "approved"
    _card_store[card_id] = card_data

    # Push status update via companion SSE
    await _push_companion_event("card_status", {
        "card_id": card_id,
        "status": "approved",
        "action": result_data.get("action"),
    })

    # Push activity to stream
    await _push_companion_event("activity", {
        "id": str(uuid.uuid4()),
        "source": "companion",
        "message": f"Approved: {card_data.get('title', card_id)}",
        "timestamp": datetime.now().isoformat(),
    })

    return JSONResponse(result_data)


@router.post("/companion/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str, request: Request) -> JSONResponse:
    """Dismiss a card — no action taken."""
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    card_data["status"] = "dismissed"
    _card_store[card_id] = card_data

    # Push status update
    await _push_companion_event("card_status", {
        "card_id": card_id,
        "status": "dismissed",
    })

    return JSONResponse({"card_id": card_id, "status": "dismissed"})


@router.patch("/companion/cards/{card_id}")
async def edit_card(card_id: str, body: CardEditRequest, request: Request) -> JSONResponse:
    """Edit a card's fields before approving."""
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    updates = body.model_dump(exclude_none=True)
    card_data.update(updates)
    _card_store[card_id] = card_data

    return JSONResponse({"card_id": card_id, "updated": list(updates.keys())})


@router.post("/companion/cards/approve-batch")
async def approve_batch(request: Request) -> JSONResponse:
    """Batch approve multiple cards."""
    body = await request.json()
    ids = body.get("ids", [])

    results = []
    for card_id in ids:
        card_data = _card_store.get(card_id)
        if card_data:
            # For batch, we just mark as approved without executing actions
            # (the full approve logic is in the single-card endpoint)
            card_data["status"] = "approved"
            results.append({"card_id": card_id, "status": "approved"})

    return JSONResponse({"approved": results, "count": len(results)})


# ---------------------------------------------------------------------------
# Companion SSE stream
# ---------------------------------------------------------------------------

@router.get("/companion/stream")
async def companion_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for the companion screen.

    Delivers transcript segments, cards, card status updates,
    and activity events in real-time.
    """
    conn_id = str(uuid.uuid4())[:8]
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _companion_queues[conn_id] = queue

    logger.info("Companion SSE client connected: %s", conn_id)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                    yield message
                except asyncio.TimeoutError:
                    yield f": heartbeat {int(time.time())}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _companion_queues.pop(conn_id, None)
            logger.info("Companion SSE client disconnected: %s", conn_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
