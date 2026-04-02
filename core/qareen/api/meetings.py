"""Qareen API — Meeting/Session recording engine.

Migrated from the old companion service. Manages meeting lifecycle,
stores transcripts in qareen.db sessions table, and exports to vault.
Routes live under /companion/ to match the Meeting.tsx frontend.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(tags=["meetings"])

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
VAULT_DIR = Path.home() / "vault"

# ---------------------------------------------------------------------------
# In-memory meeting state (one active meeting at a time)
# ---------------------------------------------------------------------------

class MeetingState:
    def __init__(self):
        self.reset()
        self._push_fn = None

    def reset(self):
        self.id: str | None = None
        self.title: str = ""
        self.status: str = "idle"
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.participants: list[str] = []
        self.transcript: list[dict] = []
        self.notes: dict[str, list[str]] = {}
        self.summary: str = ""

    @property
    def duration_seconds(self) -> int:
        if not self.started_at: return 0
        return int((self.ended_at or time.time()) - self.started_at)

    @property
    def duration_str(self) -> str:
        s = self.duration_seconds
        return f"{s // 60:02d}:{s % 60:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "state": self.status,
                "started_at": self.started_at, "duration": self.duration_str,
                "duration_seconds": self.duration_seconds, "participants": self.participants,
                "transcript": self.transcript, "notes": self.notes, "summary": self.summary}

    async def broadcast(self, event_type: str, data: dict):
        if self._push_fn:
            await self._push_fn(event_type, data)

_meeting = MeetingState()

def wire_meetings_to_companion(push_fn) -> None:
    _meeting._push_fn = push_fn
    logger.info("Meeting engine wired to companion SSE stream")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _parse_row(row) -> dict:
    """Parse a sessions row into a meeting dict."""
    outcome = json.loads(row["outcome"] or "{}") if row["outcome"] else {}
    started, ended = row["started_at"] or "", row["ended_at"]
    duration = 0
    if started and ended:
        try: duration = int((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds())
        except ValueError: pass
    return {
        "id": row["id"], "title": outcome.get("title", "Untitled Meeting"),
        "date": started, "duration_seconds": duration,
        "has_transcript": (row["utterance_count"] or 0) > 0,
        "has_summary": bool(row["transcript_summary"]),
        "summary": row["transcript_summary"] or "",
        "transcript": outcome.get("transcript", []),
        "notes": outcome.get("notes", {}),
        "participants": outcome.get("participants", []),
        "audio_path": outcome.get("audio_path", ""),
    }

def _list_sessions(limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE agent_id = 'meeting' ORDER BY started_at DESC LIMIT ?",
            (limit,)).fetchall()
    return [_parse_row(r) for r in rows]

def _get_session(meeting_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (meeting_id,)).fetchone()
    return _parse_row(row) if row else None

def _create_session(mid: str, title: str, participants: list[str]):
    with _db() as conn:
        conn.execute("INSERT INTO sessions (id,agent_id,operator_id,status,started_at,outcome) VALUES (?,?,?,?,?,?)",
                     (mid, "meeting", "operator", "active", datetime.now().isoformat(),
                      json.dumps({"title": title, "participants": participants})))

def _update_session(mid: str, **kw):
    sets = [f"{k}=?" for k in kw]
    with _db() as conn:
        conn.execute(f"UPDATE sessions SET {','.join(sets)} WHERE id=?", [*kw.values(), mid])

def _delete_session(mid: str) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM sessions WHERE id=?", (mid,)).rowcount > 0

def _persist_meeting(m: MeetingState):
    if not m.id: return
    outcome = {"title": m.title, "participants": m.participants,
               "transcript": m.transcript, "notes": m.notes}
    _update_session(m.id, status="ended", ended_at=datetime.now().isoformat(),
                    outcome=json.dumps(outcome, ensure_ascii=False),
                    transcript_summary=m.summary, utterance_count=len(m.transcript))

# ---------------------------------------------------------------------------
# Vault export
# ---------------------------------------------------------------------------

def _export_to_vault(m: MeetingState) -> Path | None:
    if not m.id or not m.transcript: return None
    now = datetime.now()
    slug = (m.title or "untitled").lower().replace(" ", "-")[:40]
    path = VAULT_DIR / "log" / "sessions" / f"{now.strftime('%Y-%m-%d')}-{slug}.md"

    content = f"""---
title: "{m.title or 'Untitled Meeting'}"
type: session
date: {now.strftime('%Y-%m-%d')}
duration: "{m.duration_str}"
participants: {json.dumps(m.participants)}
tags: [meeting, session]
source_ref: meeting/{m.id}
---

"""
    content += (m.summary or "*(No summary generated)*") + "\n"
    if m.notes:
        content += "\n## Notes\n\n"
        for topic, items in m.notes.items():
            content += f"### {topic}\n" + "".join(f"- {i}\n" for i in items) + "\n"
    content += "\n---\n\n## Full Transcript\n\n"
    for b in m.transcript:
        content += f"**{b.get('speaker','?')}** [{b.get('start_time','')}]: {b.get('text','')}\n\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    logger.info("Meeting exported to vault: %s", path)
    return path

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateReq(BaseModel):
    title: str = ""
    participants: list[str] = Field(default_factory=list)

class StartReq(BaseModel):
    source: str = "remote"

class NoteReq(BaseModel):
    text: str
    topic: str = "Manual Notes"

# ---------------------------------------------------------------------------
# Transcript ingestion (called via EventBus from VoiceManager)
# ---------------------------------------------------------------------------

async def on_transcript_event(event) -> None:
    if _meeting.status != "active": return
    payload = event.payload if isinstance(event.payload, dict) else {}
    text = payload.get("text", "")
    if not text or text.startswith("["): return

    elapsed = time.time() - (_meeting.started_at or time.time())
    block = {"speaker": payload.get("speaker", "You"), "text": text,
             "timestamp": elapsed, "start_time": f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}"}
    _meeting.transcript.append(block)
    await _meeting.broadcast("transcript", block)
    logger.info("Meeting transcript: [%s] %s", block["start_time"], text[:60])

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/companion/health")
async def health(request: Request):
    vm = getattr(request.app.state, "voice_manager", None)
    return JSONResponse({"status": "ok", "voice_ready": vm is not None,
                         "stt_engine": vm._stt_engine if vm else None,
                         "meeting_active": _meeting.status in ("active", "paused"),
                         "meeting_id": _meeting.id})

METADATA_DIR = Path.home() / ".aos" / "meetings" / "metadata"


def _list_metadata_sessions(limit: int = 50) -> list[dict]:
    """Read session records from the metadata JSON directory.

    This is the authoritative store for meeting history — the metadata
    files are written by the companion service after each meeting ends.
    Falls back gracefully if the directory doesn't exist.
    """
    if not METADATA_DIR.is_dir():
        return []
    files = sorted(METADATA_DIR.glob("*.json"), reverse=True)[:limit]
    results: list[dict] = []
    for f in files:
        try:
            raw = json.loads(f.read_text())
            results.append({
                "id": raw.get("id", f.stem),
                "title": raw.get("title", "Untitled"),
                "date": raw.get("date", ""),
                "duration_seconds": int(raw.get("duration_seconds", 0)),
                "has_transcript": bool(raw.get("transcript")),
                "has_summary": bool(raw.get("summary")),
                "summary_preview": (raw.get("summary", "") or "")[:120],
                "audio_path": raw.get("audio_path", ""),
            })
        except Exception:
            continue
    return results


@router.get("/companion/meetings")
async def list_meetings():
    """List meetings — combines DB sessions and metadata files, deduped by ID."""
    try:
        db_sessions = _list_sessions()
    except Exception as e:
        logger.error("Failed to list DB sessions: %s", e)
        db_sessions = []

    try:
        meta_sessions = _list_metadata_sessions()
    except Exception as e:
        logger.error("Failed to list metadata sessions: %s", e)
        meta_sessions = []

    # Merge: metadata is authoritative, DB fills gaps
    seen_ids: set[str] = set()
    merged: list[dict] = []
    for s in meta_sessions:
        seen_ids.add(s["id"])
        merged.append(s)
    for s in db_sessions:
        if s["id"] not in seen_ids:
            merged.append(s)

    # Sort by date descending
    merged.sort(key=lambda s: s.get("date", ""), reverse=True)
    return JSONResponse(merged)

@router.get("/companion/meetings/{mid}")
async def get_meeting(mid: str):
    if _meeting.id == mid and _meeting.status not in ("ended", "idle"):
        return JSONResponse(_meeting.to_dict())
    data = _get_session(mid)
    return JSONResponse(data) if data else JSONResponse({"error": "Not found"}, status_code=404)

@router.delete("/companion/meetings/{mid}")
async def delete_meeting(mid: str):
    return JSONResponse({"deleted": mid}) if _delete_session(mid) else JSONResponse({"error": "Not found"}, status_code=404)

@router.post("/companion/meeting/create")
async def create_meeting(body: CreateReq):
    mid = datetime.now().strftime("%Y%m%d-%H%M%S")
    _meeting.reset()
    _meeting.id, _meeting.title = mid, body.title
    _meeting.participants = ["You"] + body.participants
    _meeting.status = "setup"
    try: _create_session(mid, body.title, _meeting.participants)
    except Exception as e: logger.error("DB create failed: %s", e)
    logger.info("Meeting created: %s", mid)
    return JSONResponse({"id": mid, "status": "created"})

@router.post("/companion/meeting/start")
async def start_meeting(body: StartReq, request: Request):
    if not _meeting.id:
        return JSONResponse({"error": "No meeting created"}, status_code=400)
    _meeting.status, _meeting.started_at = "active", time.time()
    try: _update_session(_meeting.id, status="active")
    except Exception as e: logger.error("DB update failed: %s", e)
    await _meeting.broadcast("meeting_state", {"state": "active", "meeting": _meeting.to_dict()})
    logger.info("Meeting started: %s (source: %s)", _meeting.id, body.source)
    return JSONResponse({"id": _meeting.id, "status": "active"})

@router.post("/companion/meeting/pause")
async def pause_meeting():
    if not _meeting.id or _meeting.status != "active":
        return JSONResponse({"error": "No active meeting"}, status_code=400)
    _meeting.status = "paused"
    await _meeting.broadcast("meeting_state", {"state": "paused"})
    return JSONResponse({"id": _meeting.id, "status": "paused"})

@router.post("/companion/meeting/resume")
async def resume_meeting():
    if not _meeting.id:
        return JSONResponse({"error": "No meeting"}, status_code=400)
    _meeting.status = "active"
    await _meeting.broadcast("meeting_state", {"state": "active"})
    return JSONResponse({"id": _meeting.id, "status": "active"})

@router.post("/companion/meeting/end")
async def end_meeting(request: Request):
    if not _meeting.id:
        return JSONResponse({"error": "No meeting"}, status_code=400)
    _meeting.status, _meeting.ended_at = "ending", time.time()
    await _meeting.broadcast("meeting_state", {"state": "ending"})

    # Summary
    _meeting.summary = _generate_summary(_meeting) if _meeting.transcript else ""
    if not _meeting.title and _meeting.transcript:
        _meeting.title = " ".join(b.get("text","") for b in _meeting.transcript[:5])[:50].strip() or f"Meeting {_meeting.duration_str}"
    _meeting.status = "ended"

    try: _persist_meeting(_meeting)
    except Exception as e: logger.error("Persist failed: %s", e)
    try: _export_to_vault(_meeting)
    except Exception as e: logger.error("Vault export failed: %s", e)

    await _meeting.broadcast("meeting_state", {"state": "summary", "summary": _meeting.summary})
    mid = _meeting.id
    logger.info("Meeting ended: %s (dur=%s, segs=%d)", mid, _meeting.duration_str, len(_meeting.transcript))
    return JSONResponse({"meeting_id": mid, "summary": _meeting.summary,
                         "duration": _meeting.duration_str, "segments": len(_meeting.transcript)})

@router.post("/companion/meetings/{mid}/note")
async def add_note(mid: str, body: NoteReq):
    if _meeting.id == mid:
        _meeting.notes.setdefault(body.topic, []).append(body.text)
        await _meeting.broadcast("meeting_notes", {"topic": body.topic, "notes": [body.text]})
        return JSONResponse({"added": True})
    # Persisted meeting
    data = _get_session(mid)
    if not data: return JSONResponse({"error": "Not found"}, status_code=404)
    notes = data.get("notes", {})
    notes.setdefault(body.topic, []).append(body.text)
    with _db() as conn:
        row = conn.execute("SELECT outcome FROM sessions WHERE id=?", (mid,)).fetchone()
        outcome = json.loads(row["outcome"] or "{}") if row else {}
        outcome["notes"] = notes
        conn.execute("UPDATE sessions SET outcome=? WHERE id=?", (json.dumps(outcome, ensure_ascii=False), mid))
    return JSONResponse({"added": True})

# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def _generate_summary(m: MeetingState) -> str:
    if not m.transcript: return ""
    lines = [f"## Meeting: {m.title or 'Untitled'}", f"**Duration:** {m.duration_str}",
             f"**Participants:** {', '.join(m.participants)}", f"**Segments:** {len(m.transcript)}", "",
             "## Transcript Highlights", ""]
    for b in m.transcript:
        lines.append(f"- [{b.get('start_time','')}] **{b.get('speaker','?')}**: {b.get('text','')}")
    if m.notes:
        lines.append("\n## Notes")
        for topic, items in m.notes.items():
            lines.append(f"\n### {topic}")
            lines.extend(f"- {i}" for i in items)
    return "\n".join(lines)
