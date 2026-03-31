"""Qareen SSE — Server-Sent Events stream.

Real-time event delivery from the Qareen EventBus to browser clients.
Each connected client gets its own asyncio.Queue fed by the bus.
The SSEManager tracks active connections and broadcasts events.

Endpoint: GET /api/stream

Protocol:
    id: <event_uuid>
    event: <event_type>
    data: <json_payload>

A heartbeat comment is sent every 15 seconds to keep connections alive
through proxies and load balancers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from qareen.events.bus import EventBus
from qareen.events.types import Event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

HEARTBEAT_INTERVAL = 15  # seconds


# ---------------------------------------------------------------------------
# SSE connection wrapper
# ---------------------------------------------------------------------------

@dataclass
class SSEConnection:
    """A single connected SSE client."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    connected_at: datetime = field(default_factory=datetime.now)
    events_sent: int = 0


# ---------------------------------------------------------------------------
# SSE manager
# ---------------------------------------------------------------------------

class SSEManager:
    """Manages active SSE connections and broadcasts events from the bus.

    Lifecycle:
        1. On startup, subscribe to all events on the EventBus ("*").
        2. When a client connects, create an SSEConnection with a queue.
        3. When an event arrives, serialize and enqueue to all connections.
        4. When a client disconnects, remove its connection.
    """

    def __init__(self) -> None:
        self._connections: dict[str, SSEConnection] = {}
        self._bus: EventBus | None = None

    def wire(self, bus: EventBus) -> None:
        """Subscribe to all events on the bus."""
        self._bus = bus
        bus.subscribe("*", self._on_event)
        logger.info("SSEManager wired to EventBus")

    @property
    def connection_count(self) -> int:
        """Number of active SSE connections."""
        return len(self._connections)

    def connect(self) -> SSEConnection:
        """Register a new SSE client connection."""
        conn = SSEConnection()
        self._connections[conn.id] = conn
        logger.info("SSE client connected: %s (total: %d)", conn.id, len(self._connections))
        return conn

    def disconnect(self, conn: SSEConnection) -> None:
        """Remove an SSE client connection."""
        self._connections.pop(conn.id, None)
        logger.info("SSE client disconnected: %s (total: %d)", conn.id, len(self._connections))

    async def _on_event(self, event: Event) -> None:
        """Handler called by the EventBus for every event.

        Serializes the event and pushes it to all connected client queues.
        Dead connections are cleaned up if their queues are full.
        """
        sse_data = _serialize_event(event)
        dead: list[str] = []

        for conn_id, conn in self._connections.items():
            try:
                conn.queue.put_nowait(sse_data)
                conn.events_sent += 1
            except asyncio.QueueFull:
                logger.warning("SSE queue full for %s, dropping connection", conn_id)
                dead.append(conn_id)

        for conn_id in dead:
            self._connections.pop(conn_id, None)

    def status(self) -> dict[str, Any]:
        """Return status info for diagnostics."""
        return {
            "active_connections": len(self._connections),
            "connections": [
                {
                    "id": c.id,
                    "connected_at": c.connected_at.isoformat(),
                    "events_sent": c.events_sent,
                    "queue_size": c.queue.qsize(),
                }
                for c in self._connections.values()
            ],
        }


# ---------------------------------------------------------------------------
# Singleton — created at import, wired during lifespan
# ---------------------------------------------------------------------------

sse_manager = SSEManager()


# ---------------------------------------------------------------------------
# Event serialization
# ---------------------------------------------------------------------------

def _serialize_event(event: Event) -> str:
    """Convert an Event to SSE wire format.

    Format:
        id: <uuid>
        event: <event_type>
        data: <json>

    Returns a single SSE message string ending with double newline.
    """
    event_id = str(uuid.uuid4())
    event_type = event.event_type

    # Build JSON payload from event fields
    try:
        payload = _event_to_dict(event)
    except Exception:
        logger.exception("Failed to serialize event %s", event_type)
        payload = {"event_type": event_type, "error": "serialization_failed"}

    data = json.dumps(payload, default=str)

    lines = [
        f"id: {event_id}",
        f"event: {event_type}",
        f"data: {data}",
        "",  # blank line terminates the message
        "",
    ]
    return "\n".join(lines)


def _event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an Event dataclass to a plain dict.

    Handles nested dataclass fields and non-serializable types.
    """
    from dataclasses import fields as dc_fields

    result: dict[str, Any] = {}
    for f in dc_fields(event):
        value = getattr(event, f.name)
        if isinstance(value, datetime):
            result[f.name] = value.isoformat()
        elif isinstance(value, tuple):
            result[f.name] = list(value)
        elif hasattr(value, "value"):
            # Enum-like objects
            result[f.name] = value.value
        else:
            result[f.name] = value
    return result


# ---------------------------------------------------------------------------
# SSE stream generator
# ---------------------------------------------------------------------------

async def _event_stream(conn: SSEConnection, request: Request) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE messages for one client.

    Sends queued events as they arrive, with a heartbeat comment
    every HEARTBEAT_INTERVAL seconds to keep the connection alive.

    The generator exits when the client disconnects.
    """
    try:
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for an event with timeout for heartbeat
                message = await asyncio.wait_for(
                    conn.queue.get(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                yield message
            except asyncio.TimeoutError:
                # No event within the heartbeat interval — send keepalive
                yield f": heartbeat {int(time.time())}\n\n"
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream.

    Delivers real-time events from the Qareen EventBus to the browser.
    Connect with EventSource:

        const es = new EventSource("/api/stream");
        es.addEventListener("task.created", (e) => {
            console.log(JSON.parse(e.data));
        });

    The stream sends a heartbeat comment every 15 seconds.
    """
    conn = sse_manager.connect()

    async def generate():
        try:
            async for chunk in _event_stream(conn, request):
                yield chunk
        finally:
            sse_manager.disconnect(conn)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
