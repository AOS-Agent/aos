"""Bridge API — SSE stream + message endpoint for Mission Control.

Runs as a background task inside the bridge's asyncio loop.
Localhost only, no auth (same security model as dashboard :4096).

Endpoints:
    GET  /stream   — SSE stream of live conversation events
    POST /send     — send a message to the persistent session
    GET  /history  — recent conversation events for initial load
    GET  /health   — alive check
"""

import asyncio
import json
import logging
import time
from collections import deque

from aiohttp import web

logger = logging.getLogger("bridge.api")

# Ring buffer of recent events for /history (last 200 events)
_event_history: deque[dict] = deque(maxlen=200)

# Active SSE subscribers
_subscribers: list[asyncio.Queue] = []

API_PORT = 4098


def _serialize_event(event) -> dict:
    """Convert a StreamEvent to a JSON-serializable dict."""
    from session_manager import (
        TextDelta, TextComplete, ToolStart, ToolResult,
        SessionInit, RateLimit, ApiRetry, SessionResult,
    )

    base = {"ts": time.time()}

    if isinstance(event, TextDelta):
        return {**base, "type": "text_delta", "text": event.text}
    elif isinstance(event, TextComplete):
        return {**base, "type": "text_complete", "text": event.text}
    elif isinstance(event, ToolStart):
        return {**base, "type": "tool_start", "tool_id": event.tool_id,
                "name": event.name, "preview": event.input_preview}
    elif isinstance(event, ToolResult):
        return {**base, "type": "tool_result", "tool_id": event.tool_id,
                "is_error": event.is_error, "preview": event.preview}
    elif isinstance(event, SessionInit):
        return {**base, "type": "session_init", "session_id": event.session_id,
                "model": event.model}
    elif isinstance(event, RateLimit):
        return {**base, "type": "rate_limit", "status": event.status,
                "resets_at": event.resets_at}
    elif isinstance(event, SessionResult):
        return {**base, "type": "result", "session_id": event.session_id,
                "text": event.text, "is_error": event.is_error,
                "duration_ms": event.duration_ms, "cost_usd": event.cost_usd,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "num_turns": event.num_turns}
    else:
        return {**base, "type": "unknown", "data": str(event)}


def publish_event(event):
    """Called by the renderer/session manager to broadcast an event.

    This is the hook point — call this for every event that flows through
    the bridge so Mission Control sees it in real-time.
    """
    serialized = _serialize_event(event)
    _event_history.append(serialized)

    # Push to all SSE subscribers
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(serialized)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.remove(q)


def publish_user_message(text: str, source: str = "telegram"):
    """Record a user message in the event stream."""
    msg = {
        "ts": time.time(),
        "type": "user_message",
        "text": text,
        "source": source,
    }
    _event_history.append(msg)
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


# ── Handlers ─────────────────────────────────────────────


async def handle_stream(request):
    """SSE endpoint — streams live events to Mission Control."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    logger.info(f"SSE client connected ({len(_subscribers)} total)")

    try:
        while True:
            event = await q.get()
            data = json.dumps(event)
            await response.write(f"data: {data}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        if q in _subscribers:
            _subscribers.remove(q)
        logger.info(f"SSE client disconnected ({len(_subscribers)} total)")

    return response


async def handle_send(request):
    """POST /send — send a message to the persistent session."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty message"}, status=400)

    source = body.get("source", "mission-control")

    # Record the user message
    publish_user_message(text, source=source)

    # Send to persistent session
    from session_manager import get_persistent_session
    session = get_persistent_session()

    if not session.alive:
        await session.start()

    # Collect events and publish them as they arrive
    result_event = None
    async for event in session.send(text):
        publish_event(event)
        from session_manager import SessionResult
        if isinstance(event, SessionResult):
            result_event = event

    if result_event:
        return web.json_response({
            "ok": True,
            "text": result_event.text,
            "duration_ms": result_event.duration_ms,
            "cost_usd": result_event.cost_usd,
        })
    else:
        return web.json_response({"ok": True, "text": "(no result)"})


async def handle_history(request):
    """GET /history — return recent events for initial page load."""
    return web.json_response(
        list(_event_history),
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_health(request):
    """GET /health — alive check."""
    from session_manager import get_persistent_session
    session = get_persistent_session()
    return web.json_response({
        "ok": True,
        "persistent_session": session.alive,
        "session_id": (session.session_id or "")[:16],
        "subscribers": len(_subscribers),
        "history_size": len(_event_history),
    }, headers={"Access-Control-Allow-Origin": "*"})


async def handle_options(request):
    """CORS preflight."""
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


# ── Server lifecycle ─────────────────────────────────────


async def start_api_server():
    """Start the API server as a background task. Call from bridge main."""
    app = web.Application()
    app.router.add_get("/stream", handle_stream)
    app.router.add_post("/send", handle_send)
    app.router.add_get("/history", handle_history)
    app.router.add_get("/health", handle_health)
    app.router.add_route("OPTIONS", "/send", handle_options)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", API_PORT)
    await site.start()
    logger.info(f"Bridge API started on http://127.0.0.1:{API_PORT}")
    return runner
