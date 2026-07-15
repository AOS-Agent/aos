"""Qareen API — Sentinel monitoring + control routes.

Sentinel is the autonomous iMessage agent that watches outbound messages
for trigger phrases (e.g. '@aos', 'consider it done') and produces drafts.

This router exposes read-only views into Sentinel's state plus a small
set of control actions (pause/resume, cancel/send/discard a trigger),
and a Server-Sent Events stream for live updates.

State is sourced from:
  - ~/.aos/data/comms.db                       agent_triggers table
  - ~/.aos/config/sentinel.yaml                config (enabled, paused, phrases)
  - ~/.aos/work/sentinel/drafts/<id>.md        draft files
  - ~/.aos/work/sentinel/.cursor               watcher cursor
  - ~/.aos/logs/sentinel/sent.jsonl            send log
  - ~/.aos/data/people.db                      contact resolution
  - ~/Library/Messages/chat.db                 trigger message text
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Ensure `core/` is on sys.path so sibling packages (engine.*) can be imported.
# uvicorn's worker process doesn't inherit cwd-on-sys.path the way `python -c` does.
_CORE_DIR = Path(__file__).resolve().parents[2]  # .../core
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logger = logging.getLogger(__name__)

# Eager imports (path is set above; fail fast if anything is wrong):
from engine.comms.sentinel.attributedbody import extract_text
from engine.comms.sentinel.confidence_gate import parse_draft_file
from engine.comms.sentinel.context_builder import ContextBuilder
from engine.comms.sentinel.dispatcher import send_draft

router = APIRouter(prefix="/api/sentinel", tags=["sentinel"])

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()
COMMS_DB = HOME / ".aos" / "data" / "comms.db"
PEOPLE_DB = HOME / ".aos" / "data" / "people.db"
CHAT_DB = HOME / "Library" / "Messages" / "chat.db"
CONFIG_PATH = HOME / ".aos" / "config" / "sentinel.yaml"
DRAFTS_DIR = HOME / ".aos" / "work" / "sentinel" / "drafts"
CURSOR_FILE = HOME / ".aos" / "work" / "sentinel" / ".cursor"
SENT_LOG = HOME / ".aos" / "logs" / "sentinel" / "sent.jsonl"

SENTINEL_LAUNCH_LABEL = "com.aos.sentinel"

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ServiceInfo(BaseModel):
    running: bool
    pid: Optional[int] = None
    uptime_seconds: Optional[int] = None


class WatcherInfo(BaseModel):
    cursor: Optional[int] = None


class StatusCounts(BaseModel):
    detected: int = 0
    spawning: int = 0
    researching: int = 0
    draft_ready: int = 0
    sending: int = 0
    sent: int = 0
    pending: int = 0
    blocked: int = 0
    failed: int = 0
    cancelled: int = 0


class LastTrigger(BaseModel):
    id: str
    created_at: int
    status: str


class SentinelStatusResponse(BaseModel):
    enabled: bool
    paused: bool
    service: ServiceInfo
    watcher: WatcherInfo
    counts_today: StatusCounts
    last_trigger: Optional[LastTrigger] = None
    trigger_phrases: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class TriggerSummary(BaseModel):
    id: str
    message_id: str
    person_id: Optional[str] = None
    channel: str
    trigger_phrase: str
    status: str
    task_inferred: Optional[str] = None
    confidence: Optional[str] = None
    confidence_reasons: list[str] = Field(default_factory=list)
    contact_name: Optional[str] = None
    contact_handle: Optional[str] = None
    trigger_text: Optional[str] = None
    draft_preview: Optional[str] = None
    created_at: int
    spawned_at: Optional[int] = None
    draft_at: Optional[int] = None
    decided_at: Optional[int] = None
    sent_at: Optional[int] = None


class TriggerListResponse(BaseModel):
    triggers: list[TriggerSummary]
    total: int


class ConversationMessage(BaseModel):
    direction: str
    text: str
    timestamp: str


class TriggerDetail(TriggerSummary):
    draft_frontmatter: dict[str, Any] = Field(default_factory=dict)
    draft_body: Optional[str] = None
    conversation: list[ConversationMessage] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class ActionResponse(BaseModel):
    ok: bool
    trigger_id: str
    status: str
    detail: Optional[str] = None


class ToggleResponse(BaseModel):
    ok: bool
    paused: bool
    enabled: bool


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _comms_conn() -> sqlite3.Connection:
    """Open comms.db with row factory."""
    conn = sqlite3.connect(
        str(COMMS_DB),
        timeout=2,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _people_conn() -> Optional[sqlite3.Connection]:
    """Open people.db read-only with row factory. None if not present."""
    if not PEOPLE_DB.exists():
        return None
    try:
        uri = f"file:{PEOPLE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _chat_conn() -> Optional[sqlite3.Connection]:
    """Open chat.db read-only (NOT immutable — must respect WAL)."""
    if not CHAT_DB.exists():
        return None
    try:
        uri = f"file:{CHAT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        logger.exception("Failed to read sentinel.yaml")
        return {}


def _save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False))


# ---------------------------------------------------------------------------
# Service / cursor helpers
# ---------------------------------------------------------------------------


def _read_cursor() -> Optional[int]:
    if not CURSOR_FILE.exists():
        return None
    try:
        return int(CURSOR_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _service_info() -> ServiceInfo:
    """Inspect launchctl for the Sentinel service.

    Sentinel may run as either its own LaunchAgent (com.aos.sentinel) or
    embedded inside comms-bus. We probe launchctl for the canonical label
    and degrade gracefully when neither is present.
    """
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ServiceInfo(running=False)

    if result.returncode != 0:
        return ServiceInfo(running=False)

    pid: Optional[int] = None
    for line in result.stdout.splitlines():
        if SENTINEL_LAUNCH_LABEL in line:
            parts = line.split("\t")
            pid_str = parts[0].strip() if parts else "-"
            if pid_str.isdigit():
                pid = int(pid_str)
            break

    if pid is None:
        return ServiceInfo(running=False)

    uptime: Optional[int] = None
    try:
        # ps -o etimes returns elapsed seconds
        ps = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        etimes = ps.stdout.strip()
        if etimes.isdigit():
            uptime = int(etimes)
    except (subprocess.TimeoutExpired, OSError):
        pass

    return ServiceInfo(running=True, pid=pid, uptime_seconds=uptime)


# ---------------------------------------------------------------------------
# People resolution
# ---------------------------------------------------------------------------


def _resolve_contact(
    person_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (contact_name, contact_handle).

    Handle resolution prefers chat.db (the recipient on the message's chat).
    Name resolution prefers people.db (canonical_name).
    """
    name: Optional[str] = None
    handle: Optional[str] = None

    # Name from people.db
    if person_id:
        pconn = _people_conn()
        if pconn is not None:
            try:
                row = pconn.execute(
                    "SELECT canonical_name FROM people WHERE id = ?",
                    (person_id,),
                ).fetchone()
                if row:
                    name = row["canonical_name"]
            except sqlite3.OperationalError:
                pass
            finally:
                pconn.close()

    # Handle from chat.db — find the OTHER participant of the chat that owns
    # this message. message_id is im-<rowid>.
    if message_id and message_id.startswith("im-"):
        try:
            rowid = int(message_id[3:])
        except ValueError:
            rowid = None
        if rowid is not None:
            cconn = _chat_conn()
            if cconn is not None:
                try:
                    row = cconn.execute(
                        """
                        SELECT h.id AS handle
                        FROM message m
                        JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                        JOIN chat_handle_join chj ON chj.chat_id = cmj.chat_id
                        JOIN handle h ON h.ROWID = chj.handle_id
                        WHERE m.rowid = ?
                        LIMIT 1
                        """,
                        (rowid,),
                    ).fetchone()
                    if row:
                        handle = row["handle"]
                except sqlite3.OperationalError:
                    pass
                finally:
                    cconn.close()

    # If we still have no name but do have a handle, fall back to handle
    if not name and handle:
        name = handle

    return name, handle


def _fetch_trigger_text(message_id: Optional[str]) -> Optional[str]:
    """Pull the actual text of the trigger message from chat.db."""
    if not message_id or not message_id.startswith("im-"):
        return None
    try:
        rowid = int(message_id[3:])
    except ValueError:
        return None

    cconn = _chat_conn()
    if cconn is None:
        return None
    try:
        row = cconn.execute(
            "SELECT text, attributedBody FROM message WHERE rowid = ? LIMIT 1",
            (rowid,),
        ).fetchone()
        if not row:
            return None
        text = row["text"]
        if text:
            return str(text)
        blob = row["attributedBody"]
        if blob:
            try:
                decoded = extract_text(blob)
                if decoded:
                    return decoded
            except Exception:
                logger.debug("attributedBody decode failed", exc_info=True)
        return None
    except sqlite3.OperationalError:
        return None
    finally:
        cconn.close()


def _fetch_conversation(message_id: Optional[str], limit: int = 30) -> list[ConversationMessage]:
    """Pull the last N messages on the same chat as this message.

    Returns chronological (oldest first).
    """
    if not message_id or not message_id.startswith("im-"):
        return []
    try:
        rowid = int(message_id[3:])
    except ValueError:
        return []

    cconn = _chat_conn()
    if cconn is None:
        return []
    try:
        # Find the chat_id this message belongs to
        chat_row = cconn.execute(
            "SELECT chat_id FROM chat_message_join WHERE message_id = ? LIMIT 1",
            (rowid,),
        ).fetchone()
        if not chat_row:
            return []
        chat_id = chat_row["chat_id"]

        rows = cconn.execute(
            """
            SELECT m.rowid AS rid, m.text, m.attributedBody, m.date, m.is_from_me
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.rowid
            WHERE cmj.chat_id = ? AND m.rowid <= ?
            ORDER BY m.rowid DESC
            LIMIT ?
            """,
            (chat_id, rowid, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        cconn.close()

    APPLE_EPOCH = 978307200
    out: list[ConversationMessage] = []

    for r in rows:
        content = r["text"]
        if not content and r["attributedBody"]:
            content = extract_text(r["attributedBody"]) or ""
        content = content or ""
        date_ns = r["date"]
        try:
            ts = datetime.fromtimestamp(date_ns / 1_000_000_000 + APPLE_EPOCH).isoformat()
        except (TypeError, ValueError, OSError):
            ts = ""
        out.append(
            ConversationMessage(
                direction="outbound" if r["is_from_me"] else "inbound",
                text=content,
                timestamp=ts,
            )
        )
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Draft helpers
# ---------------------------------------------------------------------------


def _parse_draft(path_str: Optional[str]) -> tuple[dict[str, Any], Optional[str]]:
    """Return (frontmatter, body). Empty dict + None if unavailable."""
    if not path_str:
        return {}, None
    path = Path(path_str)
    if not path.exists():
        return {}, None
    try:
        parsed = parse_draft_file(path)
        if parsed is None:
            return {}, None
        return parsed.frontmatter or {}, parsed.body
    except Exception:
        logger.exception("Failed to parse draft at %s", path)
        return {}, None


def _draft_preview(body: Optional[str], max_chars: int = 200) -> Optional[str]:
    if not body:
        return None
    snippet = body.strip().replace("\r\n", "\n")
    if len(snippet) <= max_chars:
        return snippet
    return snippet[:max_chars].rstrip() + "..."


def _confidence_reasons(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, list):
            return [str(x) for x in decoded]
        return [str(decoded)]
    except (ValueError, TypeError):
        return [raw]


# ---------------------------------------------------------------------------
# Trigger projection
# ---------------------------------------------------------------------------


def _row_to_summary(row: sqlite3.Row, *, include_trigger_text: bool = True,
                    include_draft_preview: bool = True) -> TriggerSummary:
    """Project an agent_triggers row to TriggerSummary."""
    person_id = row["person_id"]
    message_id = row["message_id"]

    contact_name, contact_handle = _resolve_contact(person_id, message_id)
    trigger_text = _fetch_trigger_text(message_id) if include_trigger_text else None

    draft_preview: Optional[str] = None
    if include_draft_preview:
        _, body = _parse_draft(row["draft_path"])
        draft_preview = _draft_preview(body)

    return TriggerSummary(
        id=row["id"],
        message_id=message_id,
        person_id=person_id,
        channel=row["channel"],
        trigger_phrase=row["trigger_phrase"],
        status=row["status"],
        task_inferred=row["task_inferred"],
        confidence=row["confidence"],
        confidence_reasons=_confidence_reasons(row["confidence_reasons"]),
        contact_name=contact_name,
        contact_handle=contact_handle,
        trigger_text=trigger_text,
        draft_preview=draft_preview,
        created_at=row["created_at"],
        spawned_at=row["spawned_at"],
        draft_at=row["draft_at"],
        decided_at=row["decided_at"],
        sent_at=row["sent_at"],
    )


# ---------------------------------------------------------------------------
# Routes — status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SentinelStatusResponse)
async def get_status() -> SentinelStatusResponse:
    """Return the current Sentinel runtime state and today's counts."""
    cfg = _load_config()

    # Counts for today (midnight local → now)
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = int(midnight.timestamp())

    counts = StatusCounts()
    last_trigger: Optional[LastTrigger] = None

    if COMMS_DB.exists():
        conn = _comms_conn()
        try:
            for row in conn.execute(
                """
                SELECT status, COUNT(*) AS n FROM agent_triggers
                WHERE created_at >= ?
                GROUP BY status
                """,
                (today_start,),
            ):
                status = row["status"]
                if hasattr(counts, status):
                    setattr(counts, status, row["n"])

            row = conn.execute(
                """
                SELECT id, created_at, status FROM agent_triggers
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
            if row:
                last_trigger = LastTrigger(
                    id=row["id"],
                    created_at=row["created_at"],
                    status=row["status"],
                )
        finally:
            conn.close()

    return SentinelStatusResponse(
        enabled=bool(cfg.get("enabled", True)),
        paused=bool(cfg.get("paused", False)),
        service=_service_info(),
        watcher=WatcherInfo(cursor=_read_cursor()),
        counts_today=counts,
        last_trigger=last_trigger,
        trigger_phrases=list(cfg.get("trigger_phrases", []) or []),
        channels=list(cfg.get("channels", []) or []),
    )


# ---------------------------------------------------------------------------
# Routes — list / detail
# ---------------------------------------------------------------------------


@router.get("/triggers", response_model=TriggerListResponse)
async def list_triggers(
    status: Optional[str] = Query(
        None,
        description="Comma-separated status filter, e.g. 'pending,blocked'",
    ),
    limit: int = Query(50, ge=1, le=500),
    since: Optional[int] = Query(
        None,
        description="Unix timestamp — only triggers with created_at >= since",
    ),
) -> TriggerListResponse:
    """List recent triggers, optionally filtered by status and time."""
    if not COMMS_DB.exists():
        return TriggerListResponse(triggers=[], total=0)

    where: list[str] = []
    params: list[Any] = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)

    sql = "SELECT * FROM agent_triggers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = _comms_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    triggers = [_row_to_summary(r) for r in rows]
    return TriggerListResponse(triggers=triggers, total=len(triggers))


@router.get("/triggers/{trigger_id}", response_model=TriggerDetail)
async def get_trigger(
    trigger_id: str = PathParam(..., description="Trigger ID, e.g. trg_abc"),
) -> TriggerDetail | JSONResponse:
    """Return full detail for a single trigger including draft + conversation."""
    if not COMMS_DB.exists():
        return JSONResponse({"error": "comms.db not found"}, status_code=503)

    conn = _comms_conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return JSONResponse({"error": f"Trigger not found: {trigger_id}"}, status_code=404)

    summary = _row_to_summary(row)
    frontmatter, body = _parse_draft(row["draft_path"])

    sources: list[dict[str, Any]] = []
    raw_sources = frontmatter.get("sources") if isinstance(frontmatter, dict) else None
    if isinstance(raw_sources, list):
        sources = [s for s in raw_sources if isinstance(s, dict)]

    conversation = _fetch_conversation(row["message_id"], limit=30)

    return TriggerDetail(
        **summary.model_dump(),
        draft_frontmatter=frontmatter,
        draft_body=body,
        conversation=conversation,
        sources=sources,
        error=row["error"],
    )


# ---------------------------------------------------------------------------
# Routes — actions on triggers
# ---------------------------------------------------------------------------


_CANCELLABLE = {"detected", "spawning", "researching", "draft_ready", "sending"}
_DISCARDABLE = {"pending", "blocked", "draft_ready"}
_SENDABLE = {"pending", "blocked", "draft_ready"}


def _load_row(trigger_id: str) -> sqlite3.Row | None:
    conn = _comms_conn()
    try:
        return conn.execute(
            "SELECT * FROM agent_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
    finally:
        conn.close()


@router.post("/triggers/{trigger_id}/cancel", response_model=ActionResponse)
async def cancel_trigger(
    trigger_id: str = PathParam(..., description="Trigger ID to cancel"),
) -> ActionResponse | JSONResponse:
    """Cancel an in-flight trigger (during research/spawn/send)."""
    row = _load_row(trigger_id)
    if not row:
        return JSONResponse({"error": f"Trigger not found: {trigger_id}"}, status_code=404)

    if row["status"] not in _CANCELLABLE:
        return JSONResponse(
            {"error": f"Cannot cancel trigger in status '{row['status']}'"},
            status_code=409,
        )

    now = int(time.time())
    conn = _comms_conn()
    try:
        conn.execute(
            "UPDATE agent_triggers SET status='cancelled', decided_at=? WHERE id=?",
            (now, trigger_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Sentinel trigger cancelled via API: %s", trigger_id)
    return ActionResponse(
        ok=True,
        trigger_id=trigger_id,
        status="cancelled",
        detail="Trigger cancelled",
    )


@router.post("/triggers/{trigger_id}/discard", response_model=ActionResponse)
async def discard_trigger(
    trigger_id: str = PathParam(..., description="Trigger ID to discard"),
) -> ActionResponse | JSONResponse:
    """Discard a draft that's waiting for review (pending/blocked/draft_ready)."""
    row = _load_row(trigger_id)
    if not row:
        return JSONResponse({"error": f"Trigger not found: {trigger_id}"}, status_code=404)

    if row["status"] not in _DISCARDABLE:
        return JSONResponse(
            {"error": f"Cannot discard trigger in status '{row['status']}'"},
            status_code=409,
        )

    now = int(time.time())
    conn = _comms_conn()
    try:
        conn.execute(
            "UPDATE agent_triggers SET status='cancelled', decided_at=? WHERE id=?",
            (now, trigger_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Sentinel trigger discarded via API: %s", trigger_id)
    return ActionResponse(
        ok=True,
        trigger_id=trigger_id,
        status="cancelled",
        detail="Draft discarded",
    )


@router.post("/triggers/{trigger_id}/send", response_model=ActionResponse)
async def send_trigger(
    trigger_id: str = PathParam(..., description="Trigger ID to send"),
) -> ActionResponse | JSONResponse:
    """Manually send a pending/blocked/draft_ready draft via the dispatcher."""
    row = _load_row(trigger_id)
    if not row:
        return JSONResponse({"error": f"Trigger not found: {trigger_id}"}, status_code=404)

    if row["status"] not in _SENDABLE:
        return JSONResponse(
            {"error": f"Cannot send trigger in status '{row['status']}'"},
            status_code=409,
        )

    if not row["draft_path"] or not Path(row["draft_path"]).exists():
        return JSONResponse(
            {"error": "Draft file is missing for this trigger"},
            status_code=409,
        )

    draft = parse_draft_file(Path(row["draft_path"]))
    if draft is None or not draft.body.strip():
        return JSONResponse({"error": "Draft is empty or unparseable"}, status_code=409)

    # Build the context bundle so we have a canonical name + handle.
    try:
        bundle = ContextBuilder().build(trigger_id)
    except Exception as exc:
        logger.exception("ContextBuilder failed for %s", trigger_id)
        return JSONResponse(
            {"error": f"Failed to build context: {exc}"},
            status_code=500,
        )

    # Run the blocking dispatcher in a thread.
    def _do_send() -> tuple[bool, str]:
        return send_draft(
            trigger_id,
            bundle.contact.canonical_name,
            row["channel"],
            draft.body,
            handle=bundle.contact.handle,
        )

    ok, info = await asyncio.to_thread(_do_send)

    if not ok:
        logger.warning("Sentinel manual send failed for %s: %s", trigger_id, info)
        return JSONResponse(
            {"error": f"Send failed: {info}", "trigger_id": trigger_id},
            status_code=500,
        )

    logger.info("Sentinel manual send OK for %s", trigger_id)
    return ActionResponse(
        ok=True,
        trigger_id=trigger_id,
        status="sent",
        detail=info or "Sent",
    )


# ---------------------------------------------------------------------------
# Routes — pause / resume
# ---------------------------------------------------------------------------


@router.post("/pause", response_model=ToggleResponse)
async def pause_sentinel() -> ToggleResponse:
    """Pause Sentinel — set config.paused=true."""
    cfg = _load_config()
    cfg["paused"] = True
    _save_config(cfg)
    logger.info("Sentinel paused via API")
    return ToggleResponse(
        ok=True,
        paused=True,
        enabled=bool(cfg.get("enabled", True)),
    )


@router.post("/resume", response_model=ToggleResponse)
async def resume_sentinel() -> ToggleResponse:
    """Resume Sentinel — set config.paused=false and ensure enabled."""
    cfg = _load_config()
    cfg["paused"] = False
    cfg["enabled"] = True
    _save_config(cfg)
    logger.info("Sentinel resumed via API")
    return ToggleResponse(
        ok=True,
        paused=False,
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Routes — SSE stream
# ---------------------------------------------------------------------------


_POLL_INTERVAL_S = 2.0
_HEARTBEAT_INTERVAL_S = 15.0


@router.get("/stream")
async def stream_triggers(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of trigger state changes.

    Polls agent_triggers every 2s and emits:
      - trigger_created when a new trigger row appears
      - trigger_state when an existing trigger's status changes
    """

    async def _snapshot() -> dict[str, tuple[str, int]]:
        """Return {trigger_id: (status, created_at)} for the recent window."""
        if not COMMS_DB.exists():
            return {}

        def _query() -> dict[str, tuple[str, int]]:
            conn = _comms_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT id, status, created_at FROM agent_triggers
                    WHERE created_at >= ?
                    """,
                    (int(time.time()) - 6 * 3600,),
                ).fetchall()
            finally:
                conn.close()
            return {r["id"]: (r["status"], r["created_at"]) for r in rows}

        return await asyncio.to_thread(_query)

    async def generate():
        # Initial flush so the browser opens the stream.
        yield ": connected\n\n"

        try:
            last = await _snapshot()
        except Exception:
            logger.exception("Sentinel SSE initial snapshot failed")
            last = {}

        last_heartbeat = time.monotonic()

        while True:
            if await request.is_disconnected():
                break
            try:
                await asyncio.sleep(_POLL_INTERVAL_S)
                try:
                    current = await _snapshot()
                except Exception:
                    logger.debug("Sentinel SSE snapshot failed", exc_info=True)
                    current = last

                now_ts = int(time.time())

                # New triggers
                for tid, (status, created_at) in current.items():
                    if tid not in last:
                        payload = {
                            "trigger_id": tid,
                            "status": status,
                            "created_at": created_at,
                            "timestamp": now_ts,
                        }
                        yield f"event: trigger_created\ndata: {json.dumps(payload)}\n\n"

                # State changes
                for tid, (status, _ts) in current.items():
                    prev = last.get(tid)
                    if prev is not None and prev[0] != status:
                        payload = {
                            "trigger_id": tid,
                            "status": status,
                            "previous_status": prev[0],
                            "timestamp": now_ts,
                        }
                        yield f"event: trigger_state\ndata: {json.dumps(payload)}\n\n"

                last = current

                if time.monotonic() - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
                    yield f": heartbeat {now_ts}\n\n"
                    last_heartbeat = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Sentinel SSE loop error — continuing")
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
# touched at Fri  5 Jun 2026 23:57:15 EDT
