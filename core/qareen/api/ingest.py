"""Qareen API — Ingest routes.

Endpoints consumed by external processes (bridge, work engine, hooks)
to push data into the Qareen runtime. These replace the equivalent
endpoints from the retired dashboard service.

Endpoints:
    POST   /api/activity            — log an agent action
    PATCH  /api/activity/{id}       — update an existing activity
    GET    /api/activity             — get recent activity
    POST   /api/ingest/conversations       — log a message exchange
    PATCH  /api/ingest/conversations/{id}  — update a conversation with response
    GET    /api/ingest/conversations       — get recent conversations
    POST   /api/work/notify         — push a work event to SSE subscribers
    POST   /api/sessions/hook       — handle Claude Code session lifecycle
    GET    /api/sessions            — list Claude Code sessions
    GET    /api/sessions/{id}       — get a single session
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

# ---------------------------------------------------------------------------
# Database — all ingest data lives in qareen.db
# ---------------------------------------------------------------------------

_DB_PATH = Path(os.path.expanduser("~/.aos/data/qareen.db"))

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingest_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    parent_agent TEXT,
    status TEXT DEFAULT 'running',
    summary TEXT,
    duration_ms INTEGER,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_agent
    ON ingest_activity(agent);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_ts
    ON ingest_activity(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_activity_session
    ON ingest_activity(session_id);

CREATE TABLE IF NOT EXISTS ingest_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',
    user_key TEXT NOT NULL,
    agent TEXT,
    topic_name TEXT,
    message TEXT NOT NULL,
    response TEXT,
    duration_ms INTEGER,
    message_type TEXT DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS idx_ingest_conv_ts
    ON ingest_conversations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_conv_agent
    ON ingest_conversations(agent);

CREATE TABLE IF NOT EXISTS ingest_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    working_dir TEXT,
    agent_name TEXT,
    status TEXT DEFAULT 'running',
    files_modified TEXT DEFAULT '[]',
    tools_used TEXT DEFAULT '{}',
    total_tools INTEGER DEFAULT 0,
    last_activity TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_sess_status
    ON ingest_sessions(status);
CREATE INDEX IF NOT EXISTS idx_ingest_sess_started
    ON ingest_sessions(started_at DESC);
"""

_initialized = False


@contextmanager
def _db():
    """Context manager for DB connections."""
    global _initialized
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if not _initialized:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _initialized = True
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

@router.post("/api/activity")
async def log_activity(
    agent: str,
    action: str,
    parent_agent: str | None = None,
    status: str = "completed",
    summary: str | None = None,
):
    """Log an agent activity. Returns the activity ID."""
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO ingest_activity
               (timestamp, agent, action, parent_agent, status, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_now(), agent, action, parent_agent, status, summary),
        )
        conn.commit()
        aid = cur.lastrowid

    # Push to SSE via EventBus
    _emit_sse({"type": "activity", "id": aid, "agent": agent, "action": action,
               "status": status, "summary": summary})
    return {"id": aid}


@router.patch("/api/activity/{activity_id}")
async def update_activity(
    activity_id: int,
    status: str,
    summary: str | None = None,
    duration_ms: int | None = None,
):
    """Update an activity's status."""
    with _db() as conn:
        conn.execute(
            """UPDATE ingest_activity
               SET status = ?, summary = COALESCE(?, summary),
                   duration_ms = COALESCE(?, duration_ms)
               WHERE id = ?""",
            (status, summary, duration_ms, activity_id),
        )
        conn.commit()
    return {"ok": True}


@router.get("/api/activity")
async def get_activity(limit: int = 50, agent: str | None = None):
    """Get recent activity entries."""
    with _db() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM ingest_activity WHERE agent = ? ORDER BY timestamp DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ingest_activity ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.post("/api/ingest/conversations")
async def log_conversation(request: Request):
    """Log a conversation exchange. Returns the conversation ID."""
    body = await request.json()
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO ingest_conversations
               (timestamp, channel, user_key, agent, topic_name, message,
                response, duration_ms, message_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                body.get("channel", "telegram"),
                body.get("user_key", ""),
                body.get("agent"),
                body.get("topic_name"),
                body.get("message", ""),
                body.get("response"),
                body.get("duration_ms"),
                body.get("message_type", "text"),
            ),
        )
        conn.commit()
        cid = cur.lastrowid
    return {"id": cid}


@router.patch("/api/ingest/conversations/{conv_id}")
async def update_conversation(conv_id: int, request: Request):
    """Update a conversation with the response."""
    body = await request.json()
    with _db() as conn:
        conn.execute(
            """UPDATE ingest_conversations
               SET response = ?, duration_ms = COALESCE(?, duration_ms)
               WHERE id = ?""",
            (body.get("response", ""), body.get("duration_ms"), conv_id),
        )
        conn.commit()
    return {"ok": True}


@router.get("/api/ingest/conversations")
async def get_conversations(limit: int = 50, agent: str | None = None):
    """Get recent conversations."""
    with _db() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM ingest_conversations WHERE agent = ? ORDER BY timestamp DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ingest_conversations ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Work notify — push events to SSE subscribers
# ---------------------------------------------------------------------------

@router.post("/api/work/notify")
async def work_notify(request: Request):
    """Receive work event from CLI engine and broadcast to SSE clients."""
    event = await request.json()
    _emit_sse({"type": "work", **event})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sessions hook — Claude Code session lifecycle
# ---------------------------------------------------------------------------

# Tools worth logging individually to the activity feed
_INTERESTING_TOOLS = {"Write", "Edit", "Agent", "Bash"}
_TRIVIAL_BASH = ("ls", "cat", "echo", "pwd", "which", "head", "tail", "wc",
                 "true", "false", "test ", "[ ", "printf")


def _should_log_to_feed(tool_name: str, tool_input: dict) -> bool:
    """Decide if a tool use is interesting enough for the activity feed."""
    if tool_name not in _INTERESTING_TOOLS:
        return False
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        if any(cmd.startswith(t) for t in _TRIVIAL_BASH):
            return False
        if cmd in ("git status", "git diff", "git log", "git branch"):
            return False
    return True


def _upsert_session(session_id: str, tool_name: str | None = None,
                    tool_input: dict | None = None,
                    working_dir: str | None = None) -> bool:
    """Create or update a session. Returns True if this is a new session."""
    now = _now()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM ingest_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        is_new = row is None

        if is_new:
            conn.execute(
                """INSERT INTO ingest_sessions
                   (session_id, started_at, working_dir, status,
                    files_modified, tools_used, total_tools, last_activity)
                   VALUES (?, ?, ?, 'running', '[]', '{}', 0, ?)""",
                (session_id, now, working_dir, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM ingest_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        if tool_name:
            tools = json.loads(row["tools_used"])
            tools[tool_name] = tools.get(tool_name, 0) + 1
            total = row["total_tools"] + 1

            files = json.loads(row["files_modified"])
            if tool_name in ("Write", "Edit"):
                fpath = tool_input.get("file_path", "") if tool_input else ""
                if fpath and fpath not in files:
                    files.append(fpath)

            conn.execute(
                """UPDATE ingest_sessions
                   SET tools_used = ?, total_tools = ?,
                       files_modified = ?, last_activity = ?
                   WHERE session_id = ?""",
                (json.dumps(tools), total, json.dumps(files), now, session_id),
            )

        conn.commit()
    return is_new


def _end_session(session_id: str) -> None:
    """Mark a session as completed."""
    with _db() as conn:
        conn.execute(
            "UPDATE ingest_sessions SET status = 'completed', ended_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )
        conn.commit()


def _get_session(session_id: str) -> dict | None:
    """Get a single session by ID."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM ingest_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["files_modified"] = json.loads(d.get("files_modified") or "[]")
    d["tools_used"] = json.loads(d.get("tools_used") or "{}")
    return d


def _shorten(path: str) -> str:
    return path.replace(str(Path.home()) + "/", "~/") if path else ""


def _filename(path: str) -> str:
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _log_activity_sync(agent: str, action: str, status: str = "completed",
                       summary: str | None = None,
                       session_id: str | None = None) -> int:
    """Log an activity (sync helper for session hook)."""
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO ingest_activity
               (timestamp, agent, action, status, summary, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_now(), agent, action, status, summary, session_id),
        )
        conn.commit()
        return cur.lastrowid


@router.post("/api/sessions/hook")
async def session_hook(request: Request):
    """Receive hook events from Claude Code's PostToolUse and Stop hooks."""
    body = await request.json()
    hook_type = body.get("hook_type", "")
    payload = body.get("payload", {})

    session_id = payload.get("session_id", "")
    if not session_id:
        return {"ok": False, "error": "no session_id"}

    if hook_type == "stop":
        _end_session(session_id)
        s = _get_session(session_id)
        if s:
            files = s.get("files_modified", [])
            file_count = len(files)
            total = s.get("total_tools", 0)
            if file_count > 0:
                names = [_filename(f) for f in files[:3]]
                file_str = ", ".join(names)
                if file_count > 3:
                    file_str += f" +{file_count - 3} more"
                summary = f"Edited {file_str} ({total} ops)"
            else:
                summary = f"Completed ({total} ops)"
            _log_activity_sync("claude", "Session ended", status="completed",
                               summary=summary, session_id=session_id)
        return {"ok": True}

    if hook_type == "tool":
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})
        working_dir = payload.get("cwd", "")

        is_new = _upsert_session(session_id, tool_name, tool_input, working_dir)

        if is_new:
            dir_short = _shorten(working_dir)
            project = dir_short.split("/")[-1] if dir_short else "unknown"
            _log_activity_sync("claude", "Session started", status="running",
                               summary=f"Working in {project}",
                               session_id=session_id)

        if _should_log_to_feed(tool_name, tool_input):
            if tool_name == "Bash":
                cmd = tool_input.get("command", "")[:80]
                _log_activity_sync("claude", f"Bash: {cmd}",
                                   session_id=session_id)
            elif tool_name in ("Write", "Edit"):
                fpath = _shorten(tool_input.get("file_path", ""))
                _log_activity_sync("claude", f"{tool_name}: {_filename(fpath)}",
                                   session_id=session_id)
            elif tool_name == "Agent":
                desc = tool_input.get("description", "agent")
                _log_activity_sync("claude", f"Agent: {desc}",
                                   session_id=session_id)

        return {"ok": True}

    return {"ok": True}


@router.get("/api/sessions")
async def list_sessions(limit: int = 50, status: str | None = None):
    """List Claude Code sessions."""
    with _db() as conn:
        query = "SELECT * FROM ingest_sessions WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["files_modified"] = json.loads(d.get("files_modified") or "[]")
        d["tools_used"] = json.loads(d.get("tools_used") or "{}")
        result.append(d)
    return result


@router.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get a single session by ID."""
    s = _get_session(session_id)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return s


# ---------------------------------------------------------------------------
# SSE helper — emit events to the Qareen EventBus if available
# ---------------------------------------------------------------------------

def _emit_sse(data: dict[str, Any]) -> None:
    """Best-effort emit to the Qareen EventBus for SSE broadcast.

    This function is called from sync endpoint handlers. It creates a
    lightweight Event and publishes it to the bus. If the bus isn't
    available (e.g. during testing), it silently no-ops.
    """
    try:
        from qareen.events.types import Event
        from qareen.sse import sse_manager

        # The SSEManager listens on "*" so any event gets broadcast.
        # Create a generic event with the ingest data as payload.
        event = Event(
            event_type=f"ingest.{data.get('type', 'unknown')}",
            source="ingest",
            payload=data,
        )
        # SSEManager._on_event is async but we need sync fire-and-forget.
        # Use the running event loop if available.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(sse_manager._on_event(event))
        except RuntimeError:
            pass  # No event loop — skip SSE
    except Exception:
        pass  # SSE not available
