"""Qareen API — Channel management routes.

Live communication channel status, pulled from the comms-bus service
and comms.db. No static YAML — everything is real-time.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

from ..ontology.types import ChannelType
from .schemas import ChannelListResponse, ChannelStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

COMMS_BUS_URL = "http://127.0.0.1:4099"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Map adapter names to ChannelType and display names
CHANNEL_META = {
    "imessage":       (ChannelType.SMS,       "iMessage"),   # SMS is closest enum; iMessage shares phone channel
    "whatsapp":       (ChannelType.WHATSAPP,  "WhatsApp"),
    "whatsapp_local": (ChannelType.WHATSAPP,  "WhatsApp (Local)"),
    "telegram":       (ChannelType.TELEGRAM,  "Telegram"),
    "email":          (ChannelType.EMAIL,     "Email"),
    "slack":          (ChannelType.SLACK,     "Slack"),
    "sms":            (ChannelType.SMS,       "SMS"),
    "rcs":            (ChannelType.SMS,       "RCS"),
}


def _query_db() -> dict[str, dict]:
    """Pull per-channel stats from comms.db."""
    if not COMMS_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(COMMS_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT channel,
                   COUNT(*) as total,
                   MAX(timestamp) as last_ts,
                   SUM(CASE WHEN timestamp > datetime('now', '-1 day') THEN 1 ELSE 0 END) as last_24h,
                   SUM(CASE WHEN timestamp > datetime('now', '-7 days') THEN 1 ELSE 0 END) as last_7d,
                   SUM(CASE WHEN timestamp > datetime('now', '-30 days') THEN 1 ELSE 0 END) as last_30d,
                   SUM(CASE WHEN direction = 'outbound' THEN 1 ELSE 0 END) as outbound,
                   SUM(CASE WHEN direction = 'inbound' OR direction IS NULL THEN 1 ELSE 0 END) as inbound,
                   SUM(CASE WHEN person_id IS NOT NULL AND person_id != '' THEN 1 ELSE 0 END) as resolved
            FROM messages
            GROUP BY channel
        """).fetchall()
        result = {}
        for row in rows:
            result[row["channel"]] = dict(row)
        conn.close()
        return result
    except Exception as e:
        logger.warning("Could not query comms.db: %s", e)
        return {}


async def _fetch_bus_health() -> dict[str, Any]:
    """Pull live health from the comms-bus HTTP API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{COMMS_BUS_URL}/health")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug("Could not reach comms-bus: %s", e)
    return {}


async def _fetch_bus_stats() -> dict[str, Any]:
    """Pull live stats from the comms-bus HTTP API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{COMMS_BUS_URL}/api/stats")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug("Could not reach comms-bus stats: %s", e)
    return {}


@router.get("", response_model=ChannelListResponse)
async def list_channels() -> ChannelListResponse:
    """List all communication channels with live status from comms-bus + comms.db."""
    bus_health = await _fetch_bus_health()
    bus_stats = await _fetch_bus_stats()
    db_stats = _query_db()

    adapters = bus_health.get("bus", {}).get("adapters", {})
    stats_channels = bus_stats.get("channels", {})

    # Merge all known channels (from bus + db)
    all_channel_ids = set(adapters.keys()) | set(db_stats.keys())
    channels: list[ChannelStatusResponse] = []

    for ch_id in sorted(all_channel_ids):
        adapter = adapters.get(ch_id, {})
        db = db_stats.get(ch_id, {})
        stats = stats_channels.get(ch_id, {})
        meta = CHANNEL_META.get(ch_id, (ChannelType.TELEGRAM, ch_id.title()))

        # Parse last message timestamp
        last_msg = None
        last_ts_str = stats.get("last_ts") or db.get("last_ts")
        if last_ts_str:
            try:
                last_msg = datetime.fromisoformat(last_ts_str)
            except (ValueError, TypeError):
                pass

        channels.append(ChannelStatusResponse(
            id=ch_id,
            channel_type=meta[0],
            name=meta[1],
            is_active=adapter.get("available", False),
            is_healthy=adapter.get("available", False) and not adapter.get("error"),
            last_checked=datetime.now(timezone.utc),
            messages_today=db.get("last_24h", 0) or 0,
            last_message=last_msg,
        ))

    active = sum(1 for c in channels if c.is_active)
    healthy = sum(1 for c in channels if c.is_healthy)

    return ChannelListResponse(
        channels=channels,
        total=len(channels),
        active_count=active,
        healthy_count=healthy,
    )


@router.get("/stats")
async def channel_stats():
    """Detailed per-channel stats for the comms dashboard."""
    bus_health = await _fetch_bus_health()
    bus_stats = await _fetch_bus_stats()
    db_stats = _query_db()

    adapters = bus_health.get("bus", {}).get("adapters", {})
    stats_channels = bus_stats.get("channels", {})

    all_channel_ids = set(adapters.keys()) | set(db_stats.keys())
    result = {}

    for ch_id in sorted(all_channel_ids):
        adapter = adapters.get(ch_id, {})
        db = db_stats.get(ch_id, {})
        stats = stats_channels.get(ch_id, {})
        meta = CHANNEL_META.get(ch_id, (ChannelType.TELEGRAM, ch_id.title()))

        result[ch_id] = {
            "name": meta[1],
            "available": adapter.get("available", False),
            "error": adapter.get("error"),
            # Totals from comms.db
            "total_messages": db.get("total", 0),
            "last_24h": db.get("last_24h", 0),
            "last_7d": db.get("last_7d", 0),
            "last_30d": db.get("last_30d", 0),
            "outbound": db.get("outbound", 0),
            "inbound": db.get("inbound", 0),
            "resolved": db.get("resolved", 0),
            "resolution_rate": round(db["resolved"] / db["total"] * 100, 1) if db.get("total") else 0,
            # Live from bus
            "last_message": stats.get("last_ts") or db.get("last_ts"),
            "session_ingested": stats.get("session_total", 0),
            "last_poll_count": stats.get("last_count", 0),
        }

    # Enrichment stats
    enrichment = {"enriched": 0, "pending": 0, "total": 0}
    if COMMS_DB.exists():
        try:
            conn = sqlite3.connect(str(COMMS_DB))
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as enriched
                FROM messages
            """).fetchone()
            enrichment["total"] = row[0] or 0
            enrichment["enriched"] = row[1] or 0
            enrichment["pending"] = enrichment["total"] - enrichment["enriched"]
            conn.close()
        except Exception:
            pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bus_running": bool(bus_health),
        "poll_count": bus_stats.get("poll_count", 0),
        "poll_interval": bus_stats.get("poll_interval_seconds", 0),
        "channels": result,
        "enrichment": enrichment,
        "recent_polls": bus_stats.get("recent_polls", []),
    }
