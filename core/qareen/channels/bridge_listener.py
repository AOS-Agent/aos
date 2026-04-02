"""Bridge SSE Listener — captures inbound messages from the bridge service.

The bridge (port 4098) exposes an SSE stream with live conversation events.
This listener connects to that stream and ingests user_message events into
comms.db, then emits message.received events on the Qareen EventBus.

Runs as a background task started during Qareen lifespan. Reconnects
automatically on disconnect with exponential backoff.

Uses httpx (already a Qareen dependency) for HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from qareen.events.bus import EventBus

logger = logging.getLogger(__name__)

BRIDGE_URL = "http://127.0.0.1:4098"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Track seen message timestamps to avoid duplicates across reconnects
_seen_ts: set[float] = set()
_MAX_SEEN = 500


def _store_message(msg: dict[str, Any]) -> str | None:
    """Store an inbound message in comms.db. Returns message ID or None."""
    try:
        msg_id = str(uuid.uuid4())
        source = msg.get("source", "telegram")
        text = msg.get("text", "")
        ts = msg.get("ts", 0)

        if not text:
            return None

        timestamp = (
            datetime.fromtimestamp(ts).isoformat() if ts else datetime.now().isoformat()
        )

        conn = sqlite3.connect(str(COMMS_DB), timeout=3)
        conn.execute("PRAGMA journal_mode=WAL")

        conn.execute(
            """INSERT OR IGNORE INTO messages
               (id, channel, direction, sender_id, content, timestamp, processed)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (msg_id, source, "inbound", "bridge", text, timestamp),
        )
        conn.commit()
        conn.close()

        return msg_id
    except Exception as e:
        logger.debug("Failed to store bridge message: %s", e)
        return None


async def _listen_bridge_sse(bus: EventBus) -> None:
    """Connect to bridge SSE and process events. Runs until cancelled."""
    backoff = 1

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                logger.info("Connecting to bridge SSE at %s/stream", BRIDGE_URL)

                async with client.stream("GET", f"{BRIDGE_URL}/stream") as resp:
                    if resp.status_code != 200:
                        logger.warning("Bridge SSE returned %d", resp.status_code)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
                        continue

                    backoff = 1  # Reset on successful connect
                    logger.info("Connected to bridge SSE stream")

                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk

                        # Process complete SSE messages (delimited by \n\n)
                        while "\n\n" in buffer:
                            raw_msg, buffer = buffer.split("\n\n", 1)
                            for line in raw_msg.strip().split("\n"):
                                if line.startswith("data: "):
                                    data_str = line[6:]
                                    try:
                                        data = json.loads(data_str)
                                        await _handle_bridge_event(data, bus)
                                    except json.JSONDecodeError:
                                        pass

        except asyncio.CancelledError:
            logger.info("Bridge listener cancelled")
            return
        except httpx.HTTPError as e:
            logger.debug("Bridge SSE connection error: %s", e)
        except Exception as e:
            logger.debug("Bridge listener error: %s", e)

        # Reconnect with backoff
        logger.info("Bridge SSE reconnecting in %ds", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


async def _handle_bridge_event(data: dict[str, Any], bus: EventBus) -> None:
    """Process a single event from the bridge SSE stream."""
    event_type = data.get("type", "")

    # Only capture user messages (inbound from Telegram/WhatsApp)
    if event_type != "user_message":
        return

    ts = data.get("ts", 0)

    # Deduplicate
    if ts in _seen_ts:
        return
    _seen_ts.add(ts)
    if len(_seen_ts) > _MAX_SEEN:
        # Trim oldest entries (approximate -- sets are unordered, but
        # this prevents unbounded growth)
        to_remove = list(_seen_ts)[: _MAX_SEEN // 2]
        for t in to_remove:
            _seen_ts.discard(t)

    text = data.get("text", "")
    source = data.get("source", "telegram")

    if not text:
        return

    logger.info("Bridge inbound [%s]: %s", source, text[:60])

    # Store in comms.db
    msg_id = _store_message(data)

    # Emit on the EventBus so pipelines and intelligence engine can react
    if bus and msg_id:
        from qareen.events.types import Event

        await bus.emit(Event(
            event_type="message.received",
            source=f"bridge:{source}",
            payload={
                "message_id": msg_id,
                "channel": source,
                "text": text,
                "sender": "bridge",
                "timestamp": (
                    datetime.fromtimestamp(ts).isoformat()
                    if ts
                    else datetime.now().isoformat()
                ),
            },
        ))


async def start_bridge_listener(bus: EventBus) -> asyncio.Task | None:
    """Start the bridge listener as a background task.

    Returns the task handle so it can be cancelled on shutdown.
    """
    # Seed with recent history so we don't re-process old messages
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{BRIDGE_URL}/history")
            if resp.status_code == 200:
                history = resp.json()
                for evt in history:
                    ts = evt.get("ts", 0)
                    if ts:
                        _seen_ts.add(ts)
                logger.info("Bridge history seeded: %d events", len(history))
    except Exception:
        logger.debug("Could not seed bridge history (bridge may not be running)")

    task = asyncio.create_task(_listen_bridge_sse(bus))
    logger.info("Bridge listener started")
    return task
