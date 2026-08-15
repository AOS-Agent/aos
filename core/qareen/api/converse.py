"""Qareen API — Converse (Conversation-Session engine) routes.

Converse is the supervised runtime that holds goal-directed multi-turn
conversations with real people over iMessage and Slack — the unification of
Sentinel (mode='sentinel', voice='operator') and Envoy (mode='envoy',
voice='agent') onto one primitive. Full design:
~/.aos/tmp/sessions-build/PLAN.md (§2 data model, §7 this surface).

This router is the T2c deliverable (Wave 1): it exposes list/detail/
messages/create/pause/resume/takeover/release/inject/close/approve plus an
SSE stream, cloned in shape from qareen/api/sentinel.py's proven stream.

ALL reads and writes go through core/engine/comms/converse/db.py — the
Wave 0 CRUD layer. This router never hand-writes SQL against
conversation_sessions / session_messages / session_actions; those three
tables *are* the contract (PLAN.md §7), and db.py is the only writer of
record. The one deliberate exception is read-only aggregation across
sessions inside the SSE loop, which is done by composing repeated db.py
calls (list_sessions / list_messages / list_pending_actions), not raw SQL.

Known scope limits (T2c is API-only — no supervisor daemon exists yet,
that's T3):
  - POST /sessions/{id}/inject (deliver="send") and POST /actions/{id}/approve
    (kind="send_reply") queue an outbound session_messages row
    (state='queued') exactly like apply_turn_result does for a handler
    reply — they do NOT call a channel's send() themselves (channels are
    T2a, the supervisor loop that actually sends+marks-sent is T3). A live
    converse daemon will pick up 'queued' rows and complete the send.
  - "resume: capped bumps max_messages" (PLAN.md §7) is NOT implemented:
    db.py's Wave 0 CRUD has no update path for max_messages (only set at
    create_session time). Resuming from 'capped' clears paused_reason and
    moves status to 'active' but leaves max_messages unchanged. Flagged as
    a deviation — needs a small db.py addition in a later wave.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Ensure `core/` is on sys.path so sibling packages (engine.*) can be
# imported — mirrors qareen/api/sentinel.py exactly (uvicorn's worker
# process doesn't inherit cwd-on-sys.path the way `python -c` does).
_CORE_DIR = Path(__file__).resolve().parents[2]  # .../core
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logger = logging.getLogger(__name__)

from engine.comms.converse import db as cdb
from engine.comms.converse import models as cmodels

router = APIRouter(prefix="/api/converse", tags=["converse"])

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

HOME = Path.home()
CONFIG_PATH = HOME / ".aos" / "config" / "converse.yaml"
WORK_DIR = HOME / ".aos" / "work" / "converse"
LOG_DIR = HOME / ".aos" / "logs" / "converse"

_CONFIG_DEFAULTS: dict[str, Any] = {
    "trust_level": 2,
    "max_messages": 30,
    "expires_days": 5,
    "tools": cmodels.TOOLS_NONE,
}


def _load_session_defaults() -> dict[str, Any]:
    """`defaults:` block from ~/.aos/config/converse.yaml, absent-safe.

    Falls back to the same hardcoded values as
    config/defaults/converse.yaml's `defaults:` block (PLAN.md §6) if the
    instance config hasn't been written yet (e.g. this router imported
    against a bare temp DB with no migration run).
    """
    if not CONFIG_PATH.exists():
        return dict(_CONFIG_DEFAULTS)
    try:
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        logger.exception("Failed to read converse.yaml")
        return dict(_CONFIG_DEFAULTS)
    defaults = dict(_CONFIG_DEFAULTS)
    defaults.update(cfg.get("defaults") or {})
    return defaults


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SessionOut(BaseModel):
    id: str
    mode: str
    voice: str
    channel: str
    conversation_ref: str
    counterpart_handle: str
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    mission: str
    success_criteria: Optional[str] = None
    constraints: Optional[str] = None
    tools: str
    trust_level: int
    status: str
    paused_reason: Optional[str] = None
    cursor: Optional[str] = None
    state_summary: Optional[str] = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    handling_started_at: Optional[int] = None
    turn_count: int
    sent_count: int
    error_count: int
    max_messages: int
    expires_at: Optional[int] = None
    origin: Optional[str] = None
    created_at: int
    updated_at: int
    closed_at: Optional[int] = None
    close_reason: Optional[str] = None


class MessageOut(BaseModel):
    id: str
    session_id: str
    channel_message_id: Optional[str] = None
    role: str
    direction: str
    text: str
    state: str
    attempt_count: int
    error: Optional[str] = None
    ts: str
    created_at: int


class ActionOut(BaseModel):
    id: str
    session_id: str
    kind: str
    payload: dict[str, Any]
    gate_reasons: list[str] = Field(default_factory=list)
    status: str
    created_at: int
    decided_at: Optional[int] = None
    executed_at: Optional[int] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionOut]
    total: int


class SessionDetail(SessionOut):
    messages: list[MessageOut] = Field(default_factory=list)
    pending_actions: list[ActionOut] = Field(default_factory=list)
    workspace_path: str
    log_path: str


class MessagesResponse(BaseModel):
    messages: list[MessageOut]


class ActionListResponse(BaseModel):
    actions: list[ActionOut]
    total: int


class CreateSessionRequest(BaseModel):
    mode: str
    voice: str
    channel: str
    counterpart_handle: str
    mission: str
    conversation_ref: Optional[str] = Field(
        None, description="Defaults to counterpart_handle if omitted (fine for "
        "iMessage; Slack callers should pass the D… channel id explicitly)."
    )
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    success_criteria: Optional[str] = None
    constraints: Optional[str] = None
    tools: Optional[str] = None
    trust_level: Optional[int] = None
    max_messages: Optional[int] = None
    expires_days: Optional[int] = Field(
        None, description="Convenience: converted to expires_at = now + N days."
    )
    expires_at: Optional[int] = Field(
        None, description="Explicit epoch override; takes precedence over expires_days."
    )
    state_summary: Optional[str] = None
    artifacts: Optional[list[dict[str, Any]]] = None
    origin: Optional[str] = "qareen"
    status: Optional[str] = None


class PauseRequest(BaseModel):
    reason: Optional[str] = None


class InjectRequest(BaseModel):
    text: str
    deliver: str = Field(..., description="'note' | 'send'")


class CloseRequest(BaseModel):
    reason: Optional[str] = None


class ActionDecisionResponse(BaseModel):
    ok: bool
    action: ActionOut
    message_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        decoded = json.loads(raw)
        return decoded if decoded is not None else default
    except (ValueError, TypeError):
        return default


def _session_out(s: cmodels.ConversationSession) -> SessionOut:
    d = s.to_dict()
    d["artifacts"] = _loads(d.get("artifacts"), [])
    return SessionOut(**d)


def _message_out(m: cmodels.SessionMessage) -> MessageOut:
    return MessageOut(**m.to_dict())


def _action_out(a: cmodels.SessionAction) -> ActionOut:
    d = a.to_dict()
    d["payload"] = _loads(d.get("payload"), {})
    d["gate_reasons"] = _loads(d.get("gate_reasons"), [])
    return ActionOut(**d)


def _workspace_paths(session_id: str) -> tuple[str, str]:
    return str(WORK_DIR / session_id), str(LOG_DIR / session_id)


def _resolve_status_filter(status: Optional[str]) -> Optional[Any]:
    """Map the ?status= query value to what db.list_sessions expects.

    'active' -> models.ACTIVE_STATUSES (any non-terminal status), 'all'/None
    -> None (no filter), otherwise a comma-separated list of exact statuses
    (validated against models.SESSION_STATUSES).
    """
    if status is None or status == "all":
        return None
    if status == "active":
        return cmodels.ACTIVE_STATUSES
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    for s in statuses:
        if s not in cmodels.SESSION_STATUSES:
            raise ValueError(f"invalid status: {s!r}")
    return statuses


# ---------------------------------------------------------------------------
# Routes — list / detail / messages
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    status: Optional[str] = Query(
        "active",
        description="'active' | 'all' | comma-separated exact statuses",
    ),
    channel: Optional[str] = Query(None, description="Filter by channel"),
    mode: Optional[str] = Query(None, description="Filter by mode"),
    limit: int = Query(50, ge=1, le=500),
) -> SessionListResponse | JSONResponse:
    try:
        status_filter = _resolve_status_filter(status)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    def _q():
        return cdb.list_sessions(status=status_filter, channel=channel, mode=mode, limit=limit)

    rows = await asyncio.to_thread(_q)
    sessions = [_session_out(r) for r in rows]
    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: str = PathParam(..., description="Session ID, e.g. cs_abc123"),
) -> SessionDetail | JSONResponse:
    def _q():
        s = cdb.get_session(session_id)
        if s is None:
            return None
        msgs = cdb.list_messages(session_id, limit=100)
        pending = [a for a in cdb.list_pending_actions(limit=200) if a.session_id == session_id]
        return s, msgs, pending

    result = await asyncio.to_thread(_q)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    session, msgs, pending = result
    workspace_path, log_path = _workspace_paths(session_id)
    return SessionDetail(
        **_session_out(session).model_dump(),
        messages=[_message_out(m) for m in msgs],
        pending_actions=[_action_out(a) for a in pending],
        workspace_path=workspace_path,
        log_path=log_path,
    )


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def get_session_messages(
    session_id: str = PathParam(..., description="Session ID"),
    after_id: Optional[str] = Query(
        None, description="Return only messages after this message id (incremental read)"
    ),
    limit: int = Query(200, ge=1, le=1000),
) -> MessagesResponse | JSONResponse:
    def _q():
        if cdb.get_session(session_id) is None:
            return None
        try:
            return cdb.list_messages(session_id, limit=limit, after_id=after_id)
        except ValueError as exc:
            raise exc

    try:
        rows = await asyncio.to_thread(_q)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if rows is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    return MessagesResponse(messages=[_message_out(m) for m in rows])


# ---------------------------------------------------------------------------
# Routes — create
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=SessionOut)
async def create_session(body: CreateSessionRequest) -> SessionOut | JSONResponse:
    defaults = _load_session_defaults()

    expires_at = body.expires_at
    if expires_at is None:
        expires_days = body.expires_days if body.expires_days is not None else defaults.get("expires_days")
        if expires_days:
            expires_at = cdb.now_ts() + int(expires_days) * 86400

    kwargs = dict(
        mode=body.mode,
        voice=body.voice,
        channel=body.channel,
        conversation_ref=body.conversation_ref or body.counterpart_handle,
        counterpart_handle=body.counterpart_handle,
        mission=body.mission,
        person_id=body.person_id,
        person_name=body.person_name,
        success_criteria=body.success_criteria,
        constraints=body.constraints,
        tools=body.tools or defaults.get("tools", cmodels.TOOLS_NONE),
        trust_level=body.trust_level if body.trust_level is not None else defaults.get("trust_level", 2),
        state_summary=body.state_summary,
        artifacts=body.artifacts,
        max_messages=body.max_messages if body.max_messages is not None else defaults.get("max_messages", 30),
        expires_at=expires_at,
        origin=body.origin,
    )
    if body.status:
        kwargs["status"] = body.status

    def _create():
        return cdb.create_session(**kwargs)

    try:
        session = await asyncio.to_thread(_create)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    logger.info("Converse session created via API: %s (%s/%s/%s)", session.id, session.mode, session.voice, session.channel)
    return _session_out(session)


# ---------------------------------------------------------------------------
# Routes — status transitions
# ---------------------------------------------------------------------------


def _load_row_or_404(session_id: str) -> cmodels.ConversationSession | None:
    return cdb.get_session(session_id)


@router.post("/sessions/{session_id}/pause", response_model=SessionOut)
async def pause_session(
    body: PauseRequest = PauseRequest(),
    session_id: str = PathParam(..., description="Session ID"),
) -> SessionOut | JSONResponse:
    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.is_terminal:
            raise ValueError(f"cannot pause a terminal session (status={s.status!r})")
        return cdb.set_status(
            session_id,
            cmodels.STATUS_PAUSED,
            paused_reason=cmodels.PAUSED_REASON_OPERATOR,
        )

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return _session_out(result)


@router.post("/sessions/{session_id}/resume", response_model=SessionOut)
async def resume_session(
    session_id: str = PathParam(..., description="Session ID"),
) -> SessionOut | JSONResponse:
    """paused|escalated|capped -> active.

    NOTE (deviation, see module docstring): PLAN.md §7 says resuming from
    'capped' should bump max_messages. db.py's Wave 0 CRUD has no update
    path for that column (only set at create_session time), so this only
    performs the status transition; max_messages is left as-is.
    """

    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.status not in (cmodels.STATUS_PAUSED, cmodels.STATUS_ESCALATED, cmodels.STATUS_CAPPED):
            raise ValueError(
                f"cannot resume from status={s.status!r} "
                f"(must be paused, escalated, or capped)"
            )
        return cdb.set_status(session_id, cmodels.STATUS_ACTIVE, clear_paused_reason=True)

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return _session_out(result)


@router.post("/sessions/{session_id}/takeover", response_model=SessionOut)
async def takeover_session(
    session_id: str = PathParam(..., description="Session ID"),
) -> SessionOut | JSONResponse:
    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.is_terminal:
            raise ValueError(f"cannot take over a terminal session (status={s.status!r})")
        return cdb.set_status(session_id, cmodels.STATUS_TAKEOVER, clear_paused_reason=True)

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return _session_out(result)


@router.post("/sessions/{session_id}/release", response_model=SessionOut)
async def release_session(
    session_id: str = PathParam(..., description="Session ID"),
) -> SessionOut | JSONResponse:
    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.status != cmodels.STATUS_TAKEOVER:
            raise ValueError(f"cannot release from status={s.status!r} (must be takeover)")
        return cdb.set_status(session_id, cmodels.STATUS_ACTIVE, clear_paused_reason=True)

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return _session_out(result)


@router.post("/sessions/{session_id}/close", response_model=SessionOut)
async def close_session(
    body: CloseRequest,
    session_id: str = PathParam(..., description="Session ID"),
) -> SessionOut | JSONResponse:
    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.is_terminal:
            raise ValueError(f"session already closed (status={s.status!r})")
        return cdb.set_status(
            session_id,
            cmodels.STATUS_STOPPED,
            close_reason=body.reason or cmodels.STATUS_STOPPED,
        )

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)
    return _session_out(result)


# ---------------------------------------------------------------------------
# Routes — inject (operator note or verbatim send)
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/inject", response_model=None)
async def inject_session(
    body: InjectRequest,
    session_id: str = PathParam(..., description="Session ID"),
) -> dict[str, Any] | JSONResponse:
    if body.deliver not in ("note", "send"):
        return JSONResponse({"error": "deliver must be 'note' or 'send'"}, status_code=400)
    if not body.text or not body.text.strip():
        return JSONResponse({"error": "text must not be empty"}, status_code=400)

    direction = cmodels.DIRECTION_INTERNAL if body.deliver == "note" else cmodels.DIRECTION_OUTBOUND
    state = cmodels.MSG_DONE if body.deliver == "note" else cmodels.MSG_QUEUED

    def _do():
        s = _load_row_or_404(session_id)
        if s is None:
            return None
        if s.is_terminal:
            raise ValueError(f"cannot inject into a terminal session (status={s.status!r})")

        # Two session_messages rows created within the same wall-clock
        # second have identical `created_at` (int seconds) and there's no
        # secondary sort key in schema.sql — a limit=1 re-query right after
        # insert can therefore non-deterministically return the *other*
        # row instead of the one just written. Avoid that class of bug
        # entirely: build the response from the exact values just
        # persisted (ts is explicit so it matches the DB row precisely;
        # created_at is int(time.time()) taken immediately after the
        # insert, matching db.add_message's own now_ts() to the second).
        ts_val = cdb.now_iso()
        conn = cdb.connect()
        try:
            mid = cdb.add_message(
                conn,
                session_id=session_id,
                role=cmodels.ROLE_OPERATOR,
                direction=direction,
                text=body.text,
                state=state,
                ts=ts_val,
            )
            conn.commit()
        finally:
            conn.close()
        created_at_val = cdb.now_ts()
        return {
            "id": mid,
            "session_id": session_id,
            "channel_message_id": None,
            "role": cmodels.ROLE_OPERATOR,
            "direction": direction,
            "text": body.text,
            "state": state,
            "attempt_count": 0,
            "error": None,
            "ts": ts_val,
            "created_at": created_at_val,
        }

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)

    return {
        "ok": True,
        "deliver": body.deliver,
        "message": MessageOut(**result).model_dump(),
        "message_id": result["id"],
    }


# ---------------------------------------------------------------------------
# Routes — actions (approvals)
# ---------------------------------------------------------------------------

# Best-effort cache so the SSE loop can report the *exact* decided status
# (approved vs rejected) instead of a generic "decided" when the decision
# happened via this same process (the common case — the Qareen UI). Entries
# are small and self-evicting (SSE pops on consume; anything unconsumed
# after _DECISION_CACHE_TTL_S is stale and dropped).
_recent_decisions: dict[str, dict[str, Any]] = {}
_DECISION_CACHE_TTL_S = 120


def _remember_decision(action: cmodels.SessionAction) -> None:
    _recent_decisions[action.id] = {
        "status": action.status,
        "session_id": action.session_id,
        "decided_at": action.decided_at,
        "_cached_at": time.time(),
    }


@router.get("/actions", response_model=ActionListResponse)
async def list_pending_actions(
    status: str = Query("proposed", description="Only 'proposed' is supported by the Wave 0 CRUD layer"),
    limit: int = Query(50, ge=1, le=500),
) -> ActionListResponse | JSONResponse:
    if status != cmodels.ACTION_PROPOSED:
        return JSONResponse(
            {"error": "only status='proposed' is supported (db.py has no other-status listing)"},
            status_code=400,
        )

    rows = await asyncio.to_thread(lambda: cdb.list_pending_actions(limit=limit))
    actions = [_action_out(a) for a in rows]
    return ActionListResponse(actions=actions, total=len(actions))


@router.post("/actions/{action_id}/approve", response_model=ActionDecisionResponse)
async def approve_action(
    action_id: str = PathParam(..., description="Action ID, e.g. sa_abc123"),
) -> ActionDecisionResponse | JSONResponse:
    """Approve a proposed action.

    For kind='send_reply' this queues the reply text as a new outbound
    session_messages row (state='queued') — the same shape
    apply_turn_result uses for a handler's own reply — so a running
    converse supervisor (T3) picks it up and sends it. This router does not
    itself call a channel's send() (no channel layer is wired into T2c).
    """

    def _do():
        action = cdb.decide_action(action_id, cmodels.ACTION_APPROVED)
        message_id: Optional[str] = None
        if action.kind == cmodels.ACTION_SEND_REPLY:
            payload = _loads(action.payload, {})
            text = payload.get("text") if isinstance(payload, dict) else None
            if text:
                conn = cdb.connect()
                try:
                    message_id = cdb.add_message(
                        conn,
                        session_id=action.session_id,
                        role=cmodels.ROLE_AGENT,
                        direction=cmodels.DIRECTION_OUTBOUND,
                        text=text,
                        state=cmodels.MSG_QUEUED,
                    )
                    conn.commit()
                finally:
                    conn.close()
        cdb.mark_action_executed(action_id)
        return action, message_id

    try:
        action, message_id = await asyncio.to_thread(_do)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    _remember_decision(action)
    logger.info("Converse action approved via API: %s (kind=%s)", action_id, action.kind)
    return ActionDecisionResponse(ok=True, action=_action_out(action), message_id=message_id)


@router.post("/actions/{action_id}/reject", response_model=ActionDecisionResponse)
async def reject_action(
    action_id: str = PathParam(..., description="Action ID, e.g. sa_abc123"),
) -> ActionDecisionResponse | JSONResponse:
    try:
        action = await asyncio.to_thread(cdb.decide_action, action_id, cmodels.ACTION_REJECTED)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    _remember_decision(action)
    logger.info("Converse action rejected via API: %s", action_id)
    return ActionDecisionResponse(ok=True, action=_action_out(action))


# ---------------------------------------------------------------------------
# Routes — SSE stream
# ---------------------------------------------------------------------------

_POLL_INTERVAL_S = 2.0
_HEARTBEAT_INTERVAL_S = 15.0
_SESSION_WINDOW = 200          # most-recently-updated sessions watched
_MESSAGE_TAIL_PER_SESSION = 5  # small burst window per active session


async def _sessions_snapshot() -> dict[str, cmodels.ConversationSession]:
    def _q():
        rows = cdb.list_sessions(status=None, limit=_SESSION_WINDOW)
        return {s.id: s for s in rows}

    return await asyncio.to_thread(_q)


async def _message_tail_snapshot(session_ids: list[str]) -> dict[str, set[str]]:
    """{session_id: {seen message ids}} for the small recent tail of each
    active session — bounded, so cheap even polled every 2s for a lean
    localhost-only tool."""

    def _q():
        out: dict[str, set[str]] = {}
        for sid in session_ids:
            try:
                rows = cdb.list_messages(sid, limit=_MESSAGE_TAIL_PER_SESSION)
            except Exception:
                rows = []
            out[sid] = {m.id for m in rows}
        return out

    return await asyncio.to_thread(_q)


async def _pending_actions_snapshot() -> dict[str, cmodels.SessionAction]:
    def _q():
        rows = cdb.list_pending_actions(limit=200)
        return {a.id: a for a in rows}

    return await asyncio.to_thread(_q)


def _sweep_decision_cache() -> None:
    now = time.time()
    stale = [k for k, v in _recent_decisions.items() if now - v["_cached_at"] > _DECISION_CACHE_TTL_S]
    for k in stale:
        _recent_decisions.pop(k, None)


@router.get("/stream")
async def stream_converse(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of session/message/action state changes.

    Cloned in shape from qareen/api/sentinel.py's stream_triggers (2s poll,
    15s heartbeat). Emits:
      - session_created           {id, status, mode, voice, channel, created_at}
      - session_state             {id, status, previous}
      - message_added             {session_id, message}
      - action_proposed           {id, session_id, kind}
      - action_decided            {id, session_id, status}  (status is
        exact — 'approved'/'rejected' — when the decision happened via this
        process within the last _DECISION_CACHE_TTL_S; otherwise 'decided'
        as a fallback signal to refetch GET /sessions/{id}.)
    """

    async def generate():
        yield ": connected\n\n"

        try:
            last_sessions = await _sessions_snapshot()
        except Exception:
            logger.exception("Converse SSE initial session snapshot failed")
            last_sessions = {}

        active_ids = [
            sid for sid, s in last_sessions.items() if s.status not in cmodels.TERMINAL_STATUSES
        ]
        try:
            last_messages = await _message_tail_snapshot(active_ids)
        except Exception:
            logger.exception("Converse SSE initial message snapshot failed")
            last_messages = {}

        try:
            last_actions = await _pending_actions_snapshot()
        except Exception:
            logger.exception("Converse SSE initial action snapshot failed")
            last_actions = {}

        last_heartbeat = time.monotonic()

        while True:
            if await request.is_disconnected():
                break
            try:
                await asyncio.sleep(_POLL_INTERVAL_S)
                now_ts = int(time.time())

                try:
                    current_sessions = await _sessions_snapshot()
                except Exception:
                    logger.debug("Converse SSE session snapshot failed", exc_info=True)
                    current_sessions = last_sessions

                # New sessions
                for sid, s in current_sessions.items():
                    if sid not in last_sessions:
                        payload = {
                            "id": sid,
                            "status": s.status,
                            "mode": s.mode,
                            "voice": s.voice,
                            "channel": s.channel,
                            "created_at": s.created_at,
                            "timestamp": now_ts,
                        }
                        yield f"event: session_created\ndata: {json.dumps(payload)}\n\n"

                # Session state changes
                for sid, s in current_sessions.items():
                    prev = last_sessions.get(sid)
                    if prev is not None and prev.status != s.status:
                        payload = {
                            "id": sid,
                            "status": s.status,
                            "previous": prev.status,
                            "timestamp": now_ts,
                        }
                        yield f"event: session_state\ndata: {json.dumps(payload)}\n\n"

                last_sessions = current_sessions
                active_ids = [
                    sid for sid, s in current_sessions.items()
                    if s.status not in cmodels.TERMINAL_STATUSES
                ]

                # Message tails (bounded to active sessions)
                try:
                    current_messages = await _message_tail_snapshot(active_ids)
                except Exception:
                    logger.debug("Converse SSE message snapshot failed", exc_info=True)
                    current_messages = last_messages

                new_ids: list[tuple[str, str]] = []
                for sid in active_ids:
                    seen = last_messages.get(sid, set())
                    now_seen = current_messages.get(sid, set())
                    for mid in now_seen - seen:
                        new_ids.append((sid, mid))

                if new_ids:
                    def _fetch_new(pairs: list[tuple[str, str]]):
                        out = []
                        by_session: dict[str, list[str]] = {}
                        for sid, mid in pairs:
                            by_session.setdefault(sid, []).append(mid)
                        for sid, mids in by_session.items():
                            rows = cdb.list_messages(sid, limit=_MESSAGE_TAIL_PER_SESSION)
                            wanted = set(mids)
                            out.extend(m for m in rows if m.id in wanted)
                        return out

                    fetched = await asyncio.to_thread(_fetch_new, new_ids)
                    for m in fetched:
                        payload = {
                            "session_id": m.session_id,
                            "message": _message_out(m).model_dump(),
                            "timestamp": now_ts,
                        }
                        yield f"event: message_added\ndata: {json.dumps(payload)}\n\n"

                last_messages = current_messages

                # Actions
                try:
                    current_actions = await _pending_actions_snapshot()
                except Exception:
                    logger.debug("Converse SSE action snapshot failed", exc_info=True)
                    current_actions = last_actions

                for aid, a in current_actions.items():
                    if aid not in last_actions:
                        payload = {
                            "id": aid,
                            "session_id": a.session_id,
                            "kind": a.kind,
                            "timestamp": now_ts,
                        }
                        yield f"event: action_proposed\ndata: {json.dumps(payload)}\n\n"

                _sweep_decision_cache()
                for aid, a in last_actions.items():
                    if aid not in current_actions:
                        cached = _recent_decisions.pop(aid, None)
                        status = cached["status"] if cached else "decided"
                        payload = {
                            "id": aid,
                            "session_id": a.session_id,
                            "status": status,
                            "timestamp": now_ts,
                        }
                        yield f"event: action_decided\ndata: {json.dumps(payload)}\n\n"

                last_actions = current_actions

                if time.monotonic() - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
                    yield f": heartbeat {now_ts}\n\n"
                    last_heartbeat = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Converse SSE loop error — continuing")
                await asyncio.sleep(_POLL_INTERVAL_S)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
