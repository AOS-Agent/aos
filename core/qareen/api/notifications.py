"""Qareen API — Notification routes.

Create, list, read, dismiss notifications. Pushes to SSE on create.
Stores in qareen.db. Deduplicates same-type within 5 minutes.
Prunes entries older than 30 days on write.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

AOS_DATA = Path.home() / ".aos"
DB_PATH = AOS_DATA / "data" / "qareen.db"

# Quiet hours check reads from operator.yaml
OPERATOR_PATH = AOS_DATA / "config" / "operator.yaml"

_PRUNE_DAYS = 30
_DEDUP_MINUTES = 5


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            body        TEXT,
            priority    TEXT DEFAULT 'normal',
            created_at  TEXT NOT NULL,
            read        INTEGER DEFAULT 0,
            dismissed   INTEGER DEFAULT 0,
            action_url  TEXT,
            channels    TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_created
        ON notifications(created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_read
        ON notifications(read, dismissed)
    """)


def _prune_old(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.utcnow() - timedelta(days=_PRUNE_DAYS)).isoformat()
    conn.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))


def _is_duplicate(conn: sqlite3.Connection, notif_type: str) -> bool:
    cutoff = (datetime.utcnow() - timedelta(minutes=_DEDUP_MINUTES)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE type = ? AND created_at > ? LIMIT 1",
        (notif_type, cutoff),
    ).fetchone()
    return row is not None


def _is_quiet_hours() -> bool:
    try:
        import yaml
        if not OPERATOR_PATH.exists():
            return False
        with open(OPERATOR_PATH) as f:
            data = yaml.safe_load(f) or {}
        daily = data.get("daily_loop", {})
        start = daily.get("quiet_hours_start", data.get("quiet_hours_start", "23:00"))
        end = daily.get("quiet_hours_end", data.get("quiet_hours_end", "06:00"))

        now = datetime.now()
        h, m = int(start.split(":")[0]), int(start.split(":")[1])
        start_mins = h * 60 + m
        h, m = int(end.split(":")[0]), int(end.split(":")[1])
        end_mins = h * 60 + m
        now_mins = now.hour * 60 + now.minute

        if start_mins > end_mins:
            # Wraps midnight (e.g. 23:00-06:00)
            return now_mins >= start_mins or now_mins < end_mins
        else:
            return start_mins <= now_mins < end_mins
    except Exception:
        return False


def _is_type_enabled(notif_type: str) -> bool:
    try:
        import yaml
        if not OPERATOR_PATH.exists():
            return True
        with open(OPERATOR_PATH) as f:
            data = yaml.safe_load(f) or {}
        prefs = data.get("notifications", {})
        return prefs.get(notif_type, True)
    except Exception:
        return True


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["read"] = bool(d.get("read"))
    d["dismissed"] = bool(d.get("dismissed"))
    if d.get("channels"):
        try:
            d["channels"] = json.loads(d["channels"])
        except (json.JSONDecodeError, TypeError):
            d["channels"] = []
    else:
        d["channels"] = []
    return d


@router.post("")
async def create_notification(request: Request) -> JSONResponse:
    """Create a notification. Deduplicates, checks quiet hours, pushes via SSE."""
    body = await request.json()

    notif_type = body.get("type", "general")
    title = body.get("title", "")
    text = body.get("body", "")
    priority = body.get("priority", "normal")
    action_url = body.get("action_url")

    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    # Check if this notification type is enabled
    if not _is_type_enabled(notif_type):
        return JSONResponse({"status": "suppressed", "reason": "type_disabled"})

    conn = _get_db()
    _ensure_table(conn)

    # Deduplicate — same type within 5 minutes
    if _is_duplicate(conn, notif_type):
        conn.close()
        return JSONResponse({"status": "suppressed", "reason": "duplicate"})

    # Determine delivery channels
    channels = ["app"]  # App always gets it

    # Check quiet hours — during quiet, only urgent + service_alerts go to external channels
    quiet = _is_quiet_hours()
    if not quiet or priority == "urgent" or notif_type == "service_alert":
        # TODO: check if Telegram is linked and enabled
        channels.append("telegram")

    notif_id = f"n_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow().isoformat()

    conn.execute(
        """INSERT INTO notifications (id, type, title, body, priority, created_at, action_url, channels)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (notif_id, notif_type, title, text, priority, now, action_url, json.dumps(channels)),
    )
    _prune_old(conn)
    conn.commit()

    notif = {
        "id": notif_id,
        "type": notif_type,
        "title": title,
        "body": text,
        "priority": priority,
        "created_at": now,
        "action_url": action_url,
        "channels": channels,
        "read": False,
    }

    # Push to SSE (app delivery)
    try:
        from qareen.api.companion import _push_companion_event
        await _push_companion_event("notification", notif)
    except Exception:
        logger.debug("SSE push failed for notification %s", notif_id)

    conn.close()
    return JSONResponse(notif, status_code=201)


@router.get("")
async def list_notifications(request: Request) -> JSONResponse:
    """List recent notifications (last 50, not dismissed)."""
    conn = _get_db()
    _ensure_table(conn)

    rows = conn.execute(
        """SELECT * FROM notifications
           WHERE dismissed = 0
           ORDER BY created_at DESC
           LIMIT 50""",
    ).fetchall()

    conn.close()
    return JSONResponse([_row_to_dict(r) for r in rows])


@router.get("/unread")
async def unread_count(request: Request) -> JSONResponse:
    """Count of unread, non-dismissed notifications."""
    conn = _get_db()
    _ensure_table(conn)

    row = conn.execute(
        "SELECT COUNT(*) as count FROM notifications WHERE read = 0 AND dismissed = 0",
    ).fetchone()

    conn.close()
    return JSONResponse({"count": row["count"] if row else 0})


@router.patch("/{notif_id}/read")
async def mark_read(notif_id: str, request: Request) -> JSONResponse:
    """Mark a notification as read."""
    conn = _get_db()
    _ensure_table(conn)
    conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok"})


@router.post("/{notif_id}/dismiss")
async def dismiss_notification(notif_id: str, request: Request) -> JSONResponse:
    """Dismiss a notification (hidden from tray)."""
    conn = _get_db()
    _ensure_table(conn)
    conn.execute("UPDATE notifications SET dismissed = 1 WHERE id = ?", (notif_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok"})


@router.post("/read-all")
async def mark_all_read(request: Request) -> JSONResponse:
    """Mark all notifications as read."""
    conn = _get_db()
    _ensure_table(conn)
    conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok"})
