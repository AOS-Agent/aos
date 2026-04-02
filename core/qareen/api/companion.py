"""Qareen API — Companion routes.

Handles text input from the companion screen, processes it through
the intelligence engine (Tier 0 regex classifier + card generator),
and manages card approval/dismiss lifecycle.

Session endpoints manage the full session lifecycle: create, pause,
resume, end, delete, list, tag, note, and auto-title.

Endpoints:
  POST /companion/input       — Process text through intelligence engine
  POST /companion/cards/{id}/approve  — Approve a card (5s undo window, then execute)
  POST /companion/cards/{id}/undo     — Undo a pending approval within 5s window
  POST /companion/cards/{id}/dismiss  — Dismiss a card
  PATCH /companion/cards/{id}         — Edit a card's fields
  POST /companion/cards/approve-batch — Batch approve multiple cards (each with undo)
  GET  /companion/stream      — SSE stream for companion events

  # Session endpoints
  POST   /companion/session/start     — Start new session
  GET    /companion/session           — Get active session
  GET    /companion/session/{id}      — Get session by ID
  PATCH  /companion/session/{id}      — Update session fields
  POST   /companion/session/{id}/pause  — Pause session
  POST   /companion/session/{id}/resume — Resume session
  POST   /companion/session/{id}/end    — End session
  DELETE /companion/session/{id}      — Delete session
  GET    /companion/sessions          — List all sessions (paginated)
  GET    /companion/sessions/paused   — List paused sessions
  POST   /companion/session/{id}/notes       — Add a note group
  PATCH  /companion/session/{id}/notes/{gid} — Update a note group
  POST   /companion/session/{id}/auto-title  — Generate title from transcript
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
from ..intelligence.types import Card

logger = logging.getLogger(__name__)

router = APIRouter(tags=["companion"])

# ---------------------------------------------------------------------------
# In-memory card store — maps card_id to serialized card dict
# ---------------------------------------------------------------------------

_card_store: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Pending undo timers — maps card_id to asyncio.Task for delayed execution
# ---------------------------------------------------------------------------

_undo_timers: dict[str, asyncio.Task] = {}

HEARTBEAT_INTERVAL = 15  # seconds
UNDO_DELAY_SECONDS = 5


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


class SessionEventsQuery(BaseModel):
    """Query parameters for session event recovery."""
    after: int = 0


class SessionStartRequest(BaseModel):
    """Request body for starting a new session."""
    type: str = Field("conversation", description="Session type: conversation | processing")
    skill: str | None = Field(None, description="Active skill name")
    title: str | None = Field(None, description="Optional initial title")


class SessionUpdateRequest(BaseModel):
    """Request body for updating a session."""
    title: str | None = None
    skill: str | None = None
    tags: list[str] | None = None
    participants: list[str] | None = None


class NoteGroupRequest(BaseModel):
    """Request body for adding a note group."""
    topic: str = Field(..., description="Note group topic/heading")
    items: list[str] = Field(default_factory=list, description="Note items")
    id: str | None = Field(None, description="Optional group ID (auto-generated if omitted)")


class NoteGroupUpdateRequest(BaseModel):
    """Request body for updating a note group."""
    topic: str | None = None
    items: list[str] | None = None
    append_items: list[str] | None = None


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
    # Reply card fields
    recipient: str | None = None
    draft_text: str | None = None
    channel: str | None = None


# ---------------------------------------------------------------------------
# SSE connections for companion stream
# ---------------------------------------------------------------------------

_companion_queues: dict[str, asyncio.Queue] = {}
_bus_wired = False


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


def wire_companion_to_bus(bus, intelligence_engine=None) -> None:
    """Subscribe to voice/transcript events on the main EventBus.

    The VoiceManager emits voice_state and transcript events to the main bus.
    This wiring forwards them to the companion SSE stream so the frontend
    VoiceIndicator and transcript segments update in real-time.

    If an intelligence_engine is provided, voice transcripts are also fed
    into the active companion session for AI processing.
    """
    global _bus_wired
    if _bus_wired:
        return
    _bus_wired = True

    async def _forward_to_companion(event):
        """Forward voice/transcript events from bus to companion stream."""
        payload = event.payload if isinstance(event.payload, dict) else {}
        await _push_companion_event(event.event_type, payload)

        # Feed FINAL voice transcripts into the intelligence engine.
        # Skip provisional (partial) transcripts — they are streaming previews
        # that will be replaced by the final transcript.
        if event.event_type == "transcript" and intelligence_engine:
            is_provisional = payload.get("is_provisional", False)
            if is_provisional:
                return

            text = payload.get("text", "")
            speaker = payload.get("speaker", "You")
            if text and not text.startswith("["):
                session_mgr = getattr(intelligence_engine, "_session_mgr", None)
                if session_mgr:
                    session = session_mgr.get_active_session()
                    if session:
                        asyncio.create_task(
                            intelligence_engine.process_input(text, speaker, session["id"])
                        )

    bus.subscribe("voice_state", _forward_to_companion)
    bus.subscribe("transcript", _forward_to_companion)
    logger.info("Companion stream wired to EventBus (voice_state, transcript)")

    # Wire meeting engine to receive transcript events.
    # This must not break the companion SSE stream if it fails.
    try:
        from .meetings import on_transcript_event, wire_meetings_to_companion

        wire_meetings_to_companion(_push_companion_event)
        bus.subscribe("transcript", on_transcript_event)
        logger.info("Meeting engine wired to transcript events")
    except ImportError:
        logger.info("Meeting module not available — companion SSE will work without meetings")
    except Exception as e:
        logger.error(
            "Meeting engine wiring failed (companion SSE still functional): %s", e,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Helper: get session manager from request
# ---------------------------------------------------------------------------

def _get_session_mgr(request: Request):
    """Get session manager from app state, or None."""
    return getattr(request.app.state, "session_manager", None)


# ---------------------------------------------------------------------------
# Routes — Briefing & Input
# ---------------------------------------------------------------------------

@router.get("/companion/briefing")
async def companion_briefing(request: Request) -> JSONResponse:
    """Get the initial briefing for the companion page.

    Called on page load. Returns a time-based greeting, active tasks,
    urgent items, and key metrics so the companion feels alive immediately.
    """
    ontology = getattr(request.app.state, "ontology", None)

    from ..intelligence.context import build_briefing

    briefing = await build_briefing(ontology)
    if briefing:
        # Also push via SSE so the stream column shows the briefing card
        await _push_companion_event("briefing", briefing)
        return JSONResponse(briefing)

    return JSONResponse({"summary": "Welcome.", "attention": [], "metrics": {}, "schedule": [], "timestamp": datetime.now().isoformat(), "id": "fallback"})


# ---------------------------------------------------------------------------
# Intent parsing — fast classifier + optional LLM for ambiguous statements
# ---------------------------------------------------------------------------

class IntentRequest(BaseModel):
    text: str = Field(..., min_length=1)

class IntentResponse(BaseModel):
    skill: str | None = None
    intent: str = ""
    people: list[str] = []
    context: list[str] = []
    confidence: float = 0.0

# Skill mapping from classifier intents
_INTENT_TO_SKILL: dict[str, str] = {
    "TASK_CREATE": "planning",
    "TASK_UPDATE": "planning",
    "RECALL": "thinking",
    "DECISION": "thinking",
    "MESSAGE_SEND": "thinking",
    "VAULT_CAPTURE": "thinking",
    "COMMAND": None,
}

# Session skill keywords (fallback if classifier returns UNKNOWN)
_SKILL_KEYWORDS: list[tuple[str, list[str]]] = [
    ("thinking", ["think", "talk", "chat", "discuss", "brainstorm", "decide", "explore", "figure", "ramble"]),
    ("meeting", ["meeting", "meet", "call", "record", "conference", "join"]),
    ("planning", ["plan", "scope", "prioritize", "roadmap", "organize", "schedule", "decompose", "break down"]),
    ("email", ["email", "inbox", "triage", "declutter", "clear", "sort", "gmail", "clean"]),
]


@router.post("/companion/intent", response_model=IntentResponse)
async def parse_intent(req: IntentRequest, request: Request) -> IntentResponse:
    """Parse a voice transcript into structured intent for SessionSetup.

    Phase 1 (instant): Regex classifier + keyword matching + people extraction
    Phase 2 (if ambiguous): Claude CLI (haiku) for full understanding
    """
    text = req.text.strip()

    # Phase 1: Fast classifier (~2ms)
    intent_result = classify(text)
    entities = intent_result.entities or []

    # Extract people and projects from entities
    people = [e.value for e in entities if e.entity_type == "person"]
    context = [e.value for e in entities if e.entity_type in ("project", "topic")]

    # Map classifier intent to session skill
    skill = _INTENT_TO_SKILL.get(intent_result.intent.name)

    # If classifier returned UNKNOWN/FILLER, try keyword matching
    if not skill:
        text_lower = text.lower()
        for sk, keywords in _SKILL_KEYWORDS:
            if any(kw in text_lower for kw in keywords):
                skill = sk
                break

    # Extract people from ontology if classifier missed them
    if not people:
        ontology = getattr(request.app.state, "ontology", None)
        if ontology:
            try:
                people_adapter = ontology.get_adapter("people")
                if people_adapter and hasattr(people_adapter, "search"):
                    # Extract capitalized words as potential names
                    import re as _re
                    name_candidates = _re.findall(r"\b[A-Z][a-z]{2,}\b", text)
                    for name in name_candidates:
                        results = people_adapter.search(name)
                        if results:
                            people.append(results[0].get("display_name", name))
            except Exception:
                pass

    confidence = intent_result.confidence

    # Phase 2: If still ambiguous and text is substantial, use Claude Haiku
    if not skill and len(text.split()) >= 4:
        try:
            prompt = (
                "Parse this voice command into a session type. "
                "Reply with ONLY one word: thinking, meeting, planning, or email.\n\n"
                f'"{text}"'
            )
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--model", "haiku",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=5)
            result = stdout.decode().strip().lower()
            if result in ("thinking", "meeting", "planning", "email"):
                skill = result
                confidence = 0.8
        except Exception:
            logger.debug("Claude intent parsing failed", exc_info=True)

    return IntentResponse(
        skill=skill,
        intent=text,
        people=people,
        context=context,
        confidence=confidence,
    )


@router.post("/companion/input", response_model=CompanionResponse)
async def companion_input(req: CompanionInput, request: Request) -> CompanionResponse:
    """Process text input through the intelligence engine.

    1. Classify intent using Tier 0 regex patterns
    2. Generate a card if the intent is actionable
    3. Assemble context (query ontology for related entities)
    4. Push everything via SSE to the companion screen
    """
    # Classify
    intent_result = classify(req.text)

    # Push transcript segment via companion SSE (only for voice input —
    # text input already adds an optimistic segment in the frontend)
    if req.source != "text":
        await _push_companion_event("transcript", {
            "id": str(uuid.uuid4()),
            "speaker": "You",
            "text": req.text,
            "timestamp": datetime.now().isoformat(),
            "is_provisional": False,
            "is_update": False,
        })

    # Assemble context — surface related people, projects, vault notes
    # Run in background so it doesn't block the response
    ontology = getattr(request.app.state, "ontology", None)
    if ontology:
        async def _assemble_bg():
            try:
                from ..intelligence.context import assemble_context
                await asyncio.wait_for(
                    assemble_context(ontology, intent_result, _push_companion_event),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.debug("Context assembly timed out (5s)")
            except Exception:
                logger.debug("Context assembly failed", exc_info=True)
        asyncio.create_task(_assemble_bg())

    # Generate card
    card = generate_card(intent_result)

    # -- Feed to intelligence engine if session active (always, regardless of card) --
    engine = getattr(request.app.state, "intelligence_engine", None)
    if engine:
        session_mgr = _get_session_mgr(request)
        if session_mgr:
            session = session_mgr.get_active_session()
            if session:
                asyncio.create_task(engine.process_input(req.text, "You", session["id"]))

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


# ---------------------------------------------------------------------------
# Routes — Session lifecycle
# ---------------------------------------------------------------------------

@router.post("/companion/session/start")
async def start_session(request: Request) -> JSONResponse:
    """Create and return a new companion session.

    Accepts optional JSON body: {type, skill, title}.
    If an active session already exists, returns it instead.
    """
    session_mgr = _get_session_mgr(request)
    engine = getattr(request.app.state, "intelligence_engine", None)

    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    # Parse optional request body
    session_type = "conversation"
    skill = None
    title = None
    try:
        body = await request.json()
        session_type = body.get("type", "conversation")
        skill = body.get("skill")
        title = body.get("title")
    except Exception:
        pass  # No body or invalid JSON — use defaults

    # Check for existing active session
    existing = session_mgr.get_active_session()
    if existing:
        return JSONResponse(existing)

    # Create via engine (loads AOS context) or fall back to session manager
    if engine:
        session_id = await engine.start_session()
        # Apply optional fields that engine.start_session doesn't handle
        if skill or title or session_type != "conversation":
            updates: dict[str, Any] = {}
            if skill:
                updates["skill"] = skill
            if title:
                updates["title"] = title
            if session_type != "conversation":
                updates["session_type"] = session_type
            if updates:
                session_mgr.update_session(session_id, **updates)
    else:
        session = session_mgr.create_session(
            session_type=session_type,
            skill=skill,
            title=title,
        )
        session_id = session["id"]

    session = session_mgr.get_session(session_id)
    await _push_companion_event("companion_session_started", {
        "session_id": session_id,
    })
    return JSONResponse(session)


@router.get("/companion/session")
async def get_active_session(request: Request) -> JSONResponse:
    """Return the active companion session, or null."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse(None)
    session = session_mgr.get_active_session()
    return JSONResponse(session)


@router.get("/companion/sessions")
async def list_sessions(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> JSONResponse:
    """List all sessions, newest first. Supports pagination and status filter."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse([])
    sessions = session_mgr.list_sessions(limit=limit, offset=offset, status=status)
    return JSONResponse(sessions)


@router.get("/companion/sessions/paused")
async def list_paused_sessions(request: Request) -> JSONResponse:
    """List all paused sessions."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse([])
    sessions = session_mgr.list_sessions(status="paused")
    return JSONResponse(sessions)


# -- Legacy / specific-path endpoints (MUST come BEFORE {session_id} routes) --

@router.post("/companion/session/end")
async def end_active_session(request: Request) -> JSONResponse:
    """End the active companion session and trigger summary.

    Legacy endpoint — prefer POST /companion/session/{id}/end.
    """
    session_mgr = _get_session_mgr(request)
    engine = getattr(request.app.state, "intelligence_engine", None)

    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    active = session_mgr.get_active_session()
    if not active:
        return JSONResponse({"error": "No active session"}, status_code=404)

    session_id = active["id"]

    if engine:
        result = await engine.end_session(session_id)
    else:
        result = session_mgr.end_session(session_id) or {}

    await _push_companion_event("companion_session_ended", {
        "session_id": session_id,
    })
    return JSONResponse(result)


@router.get("/companion/session/events")
async def get_session_events(request: Request, after: int = 0) -> JSONResponse:
    """Get missed events for SSE recovery.

    Query param `after` is the last sequence_num the client received.
    Returns all events after that sequence.
    """
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse([])

    active = session_mgr.get_active_session()
    if not active:
        return JSONResponse([])

    events = session_mgr.get_events(active["id"], after_seq=after)
    return JSONResponse(events)


# -- Parameterized session routes (MUST come AFTER specific paths) --

@router.get("/companion/session/{session_id}")
async def get_session_by_id(session_id: str, request: Request) -> JSONResponse:
    """Get a session by its ID."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.get_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return JSONResponse(session)


@router.patch("/companion/session/{session_id}")
async def update_session(session_id: str, body: SessionUpdateRequest, request: Request) -> JSONResponse:
    """Update session fields (title, skill, tags, participants)."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.get_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return JSONResponse(session)

    updated = session_mgr.update_session(session_id, **updates)
    return JSONResponse(updated)


@router.post("/companion/session/{session_id}/pause")
async def pause_session(session_id: str, request: Request) -> JSONResponse:
    """Pause a session."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.pause_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    await _push_companion_event("companion_session_paused", {
        "session_id": session_id,
    })
    return JSONResponse(session)


@router.post("/companion/session/{session_id}/resume")
async def resume_session(session_id: str, request: Request) -> JSONResponse:
    """Resume a paused session."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.resume_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    await _push_companion_event("companion_session_resumed", {
        "session_id": session_id,
    })
    return JSONResponse(session)


@router.post("/companion/session/{session_id}/end")
async def end_session_by_id(session_id: str, request: Request) -> JSONResponse:
    """End a specific session by ID. Generates summary."""
    session_mgr = _get_session_mgr(request)
    engine = getattr(request.app.state, "intelligence_engine", None)

    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.get_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    if engine:
        result = await engine.end_session(session_id)
    else:
        result = session_mgr.end_session(session_id) or {}

    await _push_companion_event("companion_session_ended", {
        "session_id": session_id,
    })
    return JSONResponse(result)


@router.delete("/companion/session/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    """Delete a session and its events."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    deleted = session_mgr.delete_session(session_id)
    if not deleted:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    return JSONResponse({"deleted": True, "session_id": session_id})


# ---------------------------------------------------------------------------
# Routes — Notes
# ---------------------------------------------------------------------------

@router.post("/companion/session/{session_id}/notes")
async def add_note_group(session_id: str, body: NoteGroupRequest, request: Request) -> JSONResponse:
    """Add a note group to a session."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    group: dict[str, Any] = {
        "topic": body.topic,
        "items": body.items,
    }
    if body.id:
        group["id"] = body.id

    session = session_mgr.add_note_group(session_id, group)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    await _push_companion_event("companion_notes", {
        "session_id": session_id,
        "topic": body.topic,
        "notes": body.items,
    })
    return JSONResponse(session)


@router.patch("/companion/session/{session_id}/notes/{group_id}")
async def update_note_group(
    session_id: str,
    group_id: str,
    body: NoteGroupUpdateRequest,
    request: Request,
) -> JSONResponse:
    """Update a specific note group."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    updates = body.model_dump(exclude_none=True)
    session = session_mgr.update_note_group(session_id, group_id, updates)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    return JSONResponse(session)


# ---------------------------------------------------------------------------
# Routes — Auto-title
# ---------------------------------------------------------------------------

@router.post("/companion/session/{session_id}/auto-title")
async def auto_title(session_id: str, request: Request) -> JSONResponse:
    """Generate a title from transcript segments."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.get_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    title = session_mgr.auto_generate_title(session_id)
    if not title:
        # Fallback title
        from ..intelligence.auto_title import get_fallback_title
        title = get_fallback_title(session.get("session_type", "conversation"))
        session_mgr.update_session(session_id, title=title)

    return JSONResponse({"session_id": session_id, "title": title})


# ---------------------------------------------------------------------------
# Routes — Audio recording
# ---------------------------------------------------------------------------

@router.post("/companion/session/{session_id}/audio/start")
async def start_audio_recording(session_id: str, request: Request) -> JSONResponse:
    """Start recording audio to the session's WAV file.

    The VoiceManager writes incoming audio chunks to a WAV file at
    ~/.aos/sessions/{session_id}/audio.wav while this is active.
    """
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    session = session_mgr.get_session(session_id)
    if not session:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    voice_manager = getattr(request.app.state, "voice_manager", None)
    if not voice_manager:
        return JSONResponse({"error": "Voice manager not available"}, status_code=503)

    audio_path = session.get("audio_path")
    if not audio_path:
        # Create path if not set
        from pathlib import Path
        session_dir = Path.home() / ".aos" / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        audio_path = str(session_dir / "audio.wav")
        session_mgr.update_session(session_id, audio_path=audio_path)

    ok = voice_manager.start_recording(audio_path)
    if not ok:
        return JSONResponse({"error": "Recording already in progress"}, status_code=409)

    return JSONResponse({"session_id": session_id, "audio_path": audio_path, "recording": True})


@router.post("/companion/session/{session_id}/audio/stop")
async def stop_audio_recording(session_id: str, request: Request) -> JSONResponse:
    """Stop recording audio and update session with duration."""
    session_mgr = _get_session_mgr(request)
    if not session_mgr:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)

    voice_manager = getattr(request.app.state, "voice_manager", None)
    if not voice_manager:
        return JSONResponse({"error": "Voice manager not available"}, status_code=503)

    audio_path, duration = voice_manager.stop_recording()
    if audio_path:
        session_mgr.update_session(
            session_id,
            audio_path=audio_path,
            audio_duration_seconds=round(duration, 2),
        )

    return JSONResponse({
        "session_id": session_id,
        "audio_path": audio_path,
        "duration_seconds": round(duration, 2),
        "recording": False,
    })


# ---------------------------------------------------------------------------
# Routes — Cards
# ---------------------------------------------------------------------------

async def _execute_card_action(
    card_id: str,
    card_data: dict[str, Any],
    registry: Any,
    ontology: Any,
) -> dict[str, Any]:
    """Execute the action associated with a card.

    Maps card_type to the appropriate action in the registry.
    Returns a result dict with action name and result on success,
    or error info on failure.
    """
    card_type = card_data.get("card_type")
    result_data: dict[str, Any] = {"card_id": card_id, "status": "approved"}

    try:
        if card_type == "task":
            is_update = card_data.get("is_update", False)

            if is_update:
                result = await registry.execute("complete_task", {
                    "ontology": ontology,
                    "task_id": card_data.get("task_title", ""),
                }, actor="operator")
                result_data["action"] = "complete_task"
                result_data["result"] = result
            else:
                result = await registry.execute("create_task", {
                    "ontology": ontology,
                    "title": card_data.get("task_title", card_data.get("title", "")),
                    "project": card_data.get("task_project"),
                    "priority": card_data.get("task_priority", 3),
                }, actor="operator")
                result_data["action"] = "create_task"
                result_data["result"] = result

        elif card_type == "decision":
            result = await registry.execute("create_inbox", {
                "ontology": ontology,
                "content": f"DECISION: {card_data.get('body', '')}",
                "source": "companion",
            }, actor="operator")
            result_data["action"] = "lock_decision"
            result_data["result"] = result

        elif card_type == "vault":
            result = await registry.execute("create_inbox", {
                "ontology": ontology,
                "content": card_data.get("body", ""),
                "source": "companion",
            }, actor="operator")
            result_data["action"] = "create_inbox"
            result_data["result"] = result

        elif card_type == "reply":
            result = await registry.execute("send_message", {
                "ontology": ontology,
                "recipient": card_data.get("recipient", ""),
                "text": card_data.get("draft_text", card_data.get("body", "")),
                "channel": card_data.get("channel") or None,
            }, actor="operator")
            result_data["action"] = "send_message"
            result_data["result"] = result

    except Exception as exc:
        logger.error("Card action execution failed for %s: %s", card_id, exc)
        result_data["status"] = "error"
        result_data["error"] = str(exc)

    return result_data


async def _finalize_approval(
    card_id: str,
    app_state: Any,
) -> dict[str, Any]:
    """Finalize a card approval: execute action, update stats, emit events.

    Called either immediately (non-undo flow) or after the undo delay expires.
    """
    card_data = _card_store.get(card_id)
    if not card_data:
        return {"error": f"Card not found: {card_id}"}

    card_type = card_data.get("card_type")
    registry = getattr(app_state, "action_registry", None)
    ontology = getattr(app_state, "ontology", None)

    # Execute the action
    if registry and ontology:
        result_data = await _execute_card_action(card_id, card_data, registry, ontology)
    else:
        result_data = {"card_id": card_id, "status": "approved"}

    # If action failed, revert to pending and notify
    if result_data.get("status") == "error":
        card_data["status"] = "pending"
        _card_store[card_id] = card_data
        await _push_companion_event("card_status", {
            "card_id": card_id,
            "status": "error",
            "error": result_data.get("error", "Unknown error"),
        })
        return result_data

    # Mark card as approved
    card_data["status"] = "approved"
    _card_store[card_id] = card_data

    # Increment session stats
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr:
        active = session_mgr.get_active_session()
        if active:
            session_mgr.increment_stat(active["id"], "approvals_total")
            session_mgr.increment_stat(active["id"], "approvals_approved")
            if card_type == "task" and not card_data.get("is_update", False):
                session_mgr.increment_stat(active["id"], "tasks_created")
            elif card_type == "decision":
                session_mgr.increment_stat(active["id"], "decisions_locked")

    # Push status update via companion SSE
    await _push_companion_event("card_status", {
        "card_id": card_id,
        "status": "approved",
        "action": result_data.get("action"),
        "result": result_data.get("result"),
    })

    # Push activity to stream
    await _push_companion_event("activity", {
        "id": str(uuid.uuid4()),
        "source": "companion",
        "message": f"Approved: {card_data.get('title', card_id)}",
        "timestamp": datetime.now().isoformat(),
    })

    # Clean up undo timer reference
    _undo_timers.pop(card_id, None)

    return result_data


@router.post("/companion/cards/{card_id}/approve")
async def approve_card(card_id: str, request: Request) -> JSONResponse:
    """Approve a card — starts a 5-second undo window before execution.

    The card enters 'approved_pending' status. After UNDO_DELAY_SECONDS
    the action is executed. If POST /cards/{id}/undo is called within
    that window, the timer is cancelled and the card reverts to 'pending'.
    """
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    # Cancel any existing undo timer for this card
    existing_timer = _undo_timers.pop(card_id, None)
    if existing_timer and not existing_timer.done():
        existing_timer.cancel()

    # Mark card as approved_pending
    card_data["status"] = "approved_pending"
    _card_store[card_id] = card_data

    # Push immediate status update so frontend knows undo window is active
    await _push_companion_event("card_status", {
        "card_id": card_id,
        "status": "approved_pending",
        "undo_seconds": UNDO_DELAY_SECONDS,
    })

    # Schedule delayed execution
    async def _delayed_execute():
        try:
            await asyncio.sleep(UNDO_DELAY_SECONDS)
            # Only execute if card is still in approved_pending
            current = _card_store.get(card_id)
            if current and current.get("status") == "approved_pending":
                await _finalize_approval(card_id, request.app.state)
        except asyncio.CancelledError:
            # Undo was called — card reverts, nothing to do
            pass

    timer_task = asyncio.create_task(_delayed_execute())
    _undo_timers[card_id] = timer_task

    return JSONResponse({
        "card_id": card_id,
        "status": "approved_pending",
        "undo_seconds": UNDO_DELAY_SECONDS,
    })


@router.post("/companion/cards/{card_id}/undo")
async def undo_card(card_id: str, request: Request) -> JSONResponse:
    """Undo a pending approval — cancel the delayed execution.

    Only works if the card is in 'approved_pending' status (within
    the undo window). After the window closes and execution completes,
    undo is no longer possible.
    """
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    if card_data.get("status") != "approved_pending":
        return JSONResponse(
            {"error": f"Card {card_id} is not in undo window (status: {card_data.get('status')})"},
            status_code=409,
        )

    # Cancel the delayed execution timer
    timer = _undo_timers.pop(card_id, None)
    if timer and not timer.done():
        timer.cancel()

    # Revert card to pending
    card_data["status"] = "pending"
    _card_store[card_id] = card_data

    # Push status update so frontend restores the card
    await _push_companion_event("card_status", {
        "card_id": card_id,
        "status": "pending",
    })

    return JSONResponse({"card_id": card_id, "status": "pending"})


@router.post("/companion/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str, request: Request) -> JSONResponse:
    """Dismiss a card — no action taken.

    Also cancels any active undo timer if the card was in approved_pending.
    """
    card_data = _card_store.get(card_id)
    if not card_data:
        return JSONResponse({"error": f"Card not found: {card_id}"}, status_code=404)

    # Cancel any active undo timer
    timer = _undo_timers.pop(card_id, None)
    if timer and not timer.done():
        timer.cancel()

    card_data["status"] = "dismissed"
    _card_store[card_id] = card_data

    # Increment session approval total (but not approved count)
    session_mgr = _get_session_mgr(request)
    if session_mgr:
        active = session_mgr.get_active_session()
        if active:
            session_mgr.increment_stat(active["id"], "approvals_total")

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
    """Batch approve multiple cards with undo windows.

    Each card gets its own undo timer. Cards enter 'approved_pending'
    status and execute after UNDO_DELAY_SECONDS unless individually undone.
    """
    body = await request.json()
    ids = body.get("ids", [])

    results = []
    for card_id in ids:
        card_data = _card_store.get(card_id)
        if not card_data:
            continue

        # Cancel any existing timer
        existing_timer = _undo_timers.pop(card_id, None)
        if existing_timer and not existing_timer.done():
            existing_timer.cancel()

        # Mark as approved_pending
        card_data["status"] = "approved_pending"
        _card_store[card_id] = card_data

        # Push status update
        await _push_companion_event("card_status", {
            "card_id": card_id,
            "status": "approved_pending",
            "undo_seconds": UNDO_DELAY_SECONDS,
        })

        # Schedule delayed execution
        async def _delayed_execute(cid=card_id):
            try:
                await asyncio.sleep(UNDO_DELAY_SECONDS)
                current = _card_store.get(cid)
                if current and current.get("status") == "approved_pending":
                    await _finalize_approval(cid, request.app.state)
            except asyncio.CancelledError:
                pass

        timer_task = asyncio.create_task(_delayed_execute())
        _undo_timers[card_id] = timer_task
        results.append({"card_id": card_id, "status": "approved_pending"})

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
    queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
    _companion_queues[conn_id] = queue

    logger.info("Companion SSE client connected: %s", conn_id)

    async def generate():
        # Immediate flush to unblock proxy buffers
        yield f": connected {conn_id}\n\n"
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
