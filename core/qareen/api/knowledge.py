"""Qareen API — Knowledge routes.

The Knowledge section is the unified home for Intelligence (staging
inbox), Vault (permanent library), Topics (auto-maintained wiki), and
Pipeline (observability). This router exposes the aggregate endpoints
the Knowledge UI needs.

Routes:
    GET  /api/knowledge/today    — aggregate summary for the Today landing view
    GET  /api/knowledge/health   — status strip data (one-line for the header)

Feed / library / topics / pipeline live in sibling routers:
    /api/intelligence/*  — feed, proposals, sources
    /api/vault/*         — existing vault search
    (topics + pipeline come in Part 7 + Part 9)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
VAULT_DIR = Path.home() / "vault"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# /api/knowledge/today
# ---------------------------------------------------------------------------

@router.get("/today")
async def knowledge_today() -> dict[str, Any]:
    """Everything the Today landing view needs in one request.

    Returns:
        {
            "status": { ... },        # for the status strip
            "cards": {
                "incoming": {...},    # top 5 unread briefs from last 24h
                "compiled": {...},    # captures written in last 24h
                "attention": {...},   # orphans, pending proposals, stale
            },
            "topic_activity": [...],  # topics with recent updates
            "compile_log": [...],     # recent compilation passes
        }
    """
    if not DB_PATH.exists():
        return _empty_today()

    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).isoformat()

    conn = _get_db()
    try:
        status = _build_status(conn, now)
        cards = {
            "incoming": _build_incoming_card(conn, yesterday),
            "compiled": _build_compiled_card(conn, yesterday),
            "attention": _build_attention_card(conn),
        }
        topic_activity = _build_topic_activity(conn)
        compile_log = _build_compile_log(conn)
    finally:
        conn.close()

    return {
        "status": status,
        "cards": cards,
        "topic_activity": topic_activity,
        "compile_log": compile_log,
    }


def _empty_today() -> dict[str, Any]:
    return {
        "status": {
            "feeds_healthy": False,
            "last_ingest": None,
            "pending_compilations": 0,
            "crons_healthy": False,
            "summary": "System initializing",
        },
        "cards": {
            "incoming": {"count": 0, "items": []},
            "compiled": {"count": 0, "items": []},
            "attention": {"count": 0, "items": []},
        },
        "topic_activity": [],
        "compile_log": [],
    }


# ---------------------------------------------------------------------------
# Status strip
# ---------------------------------------------------------------------------

def _build_status(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """One-line system health for the header strip."""
    last_ingest = None
    feeds_healthy = False
    pending_compilations = 0

    if _table_exists(conn, "intelligence_briefs"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest FROM intelligence_briefs"
        ).fetchone()
        last_ingest = row["latest"] if row else None
        if last_ingest:
            try:
                ingest_dt = datetime.fromisoformat(last_ingest.replace("Z", "+00:00"))
                if ingest_dt.tzinfo is None:
                    ingest_dt = ingest_dt.replace(tzinfo=timezone.utc)
                age = (now - ingest_dt).total_seconds() / 60
                feeds_healthy = age < 120  # healthy if ingest within 2h
            except Exception:
                pass

    if _table_exists(conn, "compilation_proposals"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM compilation_proposals WHERE status = 'pending'"
        ).fetchone()
        pending_compilations = row["cnt"] if row else 0

    # Human-readable one-liner for the UI
    parts = []
    if feeds_healthy:
        parts.append("Feeds healthy")
    elif last_ingest:
        parts.append("Feeds stale")
    else:
        parts.append("Feeds offline")

    if last_ingest:
        parts.append(f"Last ingest {_relative_time(last_ingest, now)}")
    if pending_compilations:
        parts.append(f"{pending_compilations} pending review")

    return {
        "feeds_healthy": feeds_healthy,
        "last_ingest": last_ingest,
        "pending_compilations": pending_compilations,
        "crons_healthy": True,  # Part 9 wires real cron status
        "summary": " · ".join(parts),
    }


def _relative_time(iso: str | None, now: datetime) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return iso
    diff = (now - dt).total_seconds()
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return f"{int(diff / 86400)}d ago"


# ---------------------------------------------------------------------------
# Today cards
# ---------------------------------------------------------------------------

def _build_incoming_card(conn: sqlite3.Connection, since: str) -> dict[str, Any]:
    """Top 5 unread briefs from the last 24h by relevance."""
    if not _table_exists(conn, "intelligence_briefs"):
        return {"count": 0, "items": []}

    total_row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM intelligence_briefs
        WHERE status = 'unread' AND created_at >= ?
        """,
        (since,),
    ).fetchone()
    total = total_row["cnt"] if total_row else 0

    rows = conn.execute(
        """
        SELECT b.id, b.title, b.url, b.platform, b.relevance_score,
               b.published_at, s.name as source_name
        FROM intelligence_briefs b
        LEFT JOIN intelligence_sources s ON b.source_id = s.id
        WHERE b.status = 'unread' AND b.created_at >= ?
        ORDER BY b.relevance_score DESC, b.published_at DESC
        LIMIT 5
        """,
        (since,),
    ).fetchall()

    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "url": r["url"],
            "platform": r["platform"],
            "source_name": r["source_name"],
            "relevance_score": r["relevance_score"],
            "published_at": r["published_at"],
        }
        for r in rows
    ]
    return {"count": total, "items": items}


def _build_compiled_card(conn: sqlite3.Connection, since: str) -> dict[str, Any]:
    """Captures written to the vault in the last 24h."""
    if not _table_exists(conn, "compilation_proposals"):
        return {"count": 0, "items": []}

    total_row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM compilation_proposals
        WHERE status IN ('auto_accepted', 'approved') AND created_at >= ?
        """,
        (since,),
    ).fetchone()
    total = total_row["cnt"] if total_row else 0

    rows = conn.execute(
        """
        SELECT id, vault_path, compilation_json, created_at, status
        FROM compilation_proposals
        WHERE status IN ('auto_accepted', 'approved') AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (since,),
    ).fetchall()

    items = []
    for r in rows:
        comp = _parse_json(r["compilation_json"]) or {}
        items.append({
            "id": r["id"],
            "vault_path": r["vault_path"],
            "topic": comp.get("topic", ""),
            "summary": (comp.get("summary") or "")[:140],
            "concepts": comp.get("concepts", [])[:5],
            "status": r["status"],
            "created_at": r["created_at"],
        })
    return {"count": total, "items": items}


def _build_attention_card(conn: sqlite3.Connection) -> dict[str, Any]:
    """Things that need the operator's eyes: pending proposals + orphans + stale docs."""
    attention: list[dict[str, Any]] = []

    # Pending proposals first — highest urgency
    if _table_exists(conn, "compilation_proposals"):
        rows = conn.execute(
            """
            SELECT id, topic_confidence, compilation_json, created_at
            FROM compilation_proposals
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT 5
            """
        ).fetchall()
        for r in rows:
            comp = _parse_json(r["compilation_json"]) or {}
            attention.append({
                "kind": "pending_proposal",
                "id": r["id"],
                "title": (comp.get("summary") or "")[:100] or "(no summary)",
                "topic": comp.get("topic", ""),
                "confidence": r["topic_confidence"],
                "created_at": r["created_at"],
            })

    # Orphan stage 3+ docs
    if _table_exists(conn, "vault_inventory"):
        rows = conn.execute(
            """
            SELECT path, title, stage, type FROM vault_inventory
            WHERE is_orphan = 1
            ORDER BY last_modified DESC
            LIMIT 5
            """
        ).fetchall()
        for r in rows:
            attention.append({
                "kind": "orphan",
                "path": r["path"],
                "title": r["title"],
                "stage": r["stage"],
                "type": r["type"],
            })

    # Total count across everything needing attention
    total = 0
    if _table_exists(conn, "compilation_proposals"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM compilation_proposals WHERE status = 'pending'"
        ).fetchone()
        total += row["cnt"] if row else 0
    if _table_exists(conn, "vault_inventory"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM vault_inventory WHERE is_orphan = 1"
        ).fetchone()
        total += row["cnt"] if row else 0

    return {"count": total, "items": attention[:10]}


def _build_topic_activity(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Topic indexes sorted by recent update, with doc counts."""
    # Read topic index files directly (they live in the vault, not the DB)
    indexes_dir = VAULT_DIR / "knowledge" / "indexes"
    if not indexes_dir.is_dir():
        return []

    topics: list[dict[str, Any]] = []
    try:
        from engine.intelligence.topics import load_index
    except Exception:
        return []

    for f in sorted(indexes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
        try:
            idx = load_index(f.stem)
            topics.append({
                "slug": idx.slug,
                "title": idx.title,
                "doc_count": idx.doc_count,
                "updated": idx.updated,
                "orientation": (idx.orientation or "")[:160],
            })
        except Exception:
            continue
    return topics


def _build_compile_log(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Last 10 compilation passes, human-readable."""
    if not _table_exists(conn, "compilation_proposals"):
        return []

    rows = conn.execute(
        """
        SELECT id, created_at, status, auto_accepted, topic_confidence,
               compilation_json, vault_path
        FROM compilation_proposals
        ORDER BY created_at DESC
        LIMIT 10
        """
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        comp = _parse_json(r["compilation_json"]) or {}
        template = comp.get("template_used", "")
        topic = comp.get("topic", "")
        status = r["status"]
        auto = bool(r["auto_accepted"])
        line = f"Compiled {template or 'capture'} into {topic or 'uncategorized'}"
        if auto:
            line += " (auto-accepted)"
        elif status == "approved":
            line += " (operator approved)"
        elif status == "rejected":
            line += " (rejected)"
        elif status == "pending":
            line += " (pending review)"
        out.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "status": status,
            "auto_accepted": auto,
            "topic_confidence": r["topic_confidence"],
            "topic": topic,
            "template": template,
            "vault_path": r["vault_path"],
            "line": line,
            "model": comp.get("model", ""),
            "provider": comp.get("provider", ""),
            "duration_ms": comp.get("duration_ms", 0),
        })
    return out


# ---------------------------------------------------------------------------
# /api/knowledge/library — vault browse (reads vault_inventory)
# ---------------------------------------------------------------------------

@router.get("/library")
async def knowledge_library(
    view: str = Query("stage", description="stage | topic"),
    stage: int | None = Query(None, ge=0, le=6),
    topic: str | None = Query(None),
    type: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """List vault documents from the vault_inventory table.

    Two navigation modes:
        view=stage  — group by stage (1..6) then list docs in the stage
        view=topic  — group by topic slug then list docs in each topic

    Plus optional filters:
        stage=N  — only docs with stage N
        topic=X  — only docs with topic slug X
        type=X   — only docs with type X (capture, research, ...)

    Returns:
        {
            "view": "stage" | "topic",
            "groups": [
                {"key": "1", "label": "Captures", "count": N, "docs": [...]},
                ...
            ],
            "total": N,
        }
    """
    if not DB_PATH.exists():
        return {"view": view, "groups": [], "total": 0}

    conn = _get_db()
    try:
        if not _table_exists(conn, "vault_inventory"):
            return {"view": view, "groups": [], "total": 0}

        conditions: list[str] = []
        params: list[Any] = []
        if stage is not None:
            conditions.append("stage = ?")
            params.append(stage)
        if topic:
            conditions.append("topic = ?")
            params.append(topic)
        if type:
            conditions.append("type = ?")
            params.append(type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM vault_inventory {where}", params,
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = conn.execute(
            f"""
            SELECT path, stage, type, title, topic, has_summary, has_concepts,
                   has_topic, has_source_url, backlink_count, is_orphan,
                   issues, last_modified, word_count
            FROM vault_inventory
            {where}
            ORDER BY last_modified DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()

        docs = [_inventory_row_to_dict(r) for r in rows]

        # Group results
        groups: list[dict[str, Any]] = []
        if view == "topic":
            by_topic: dict[str, list[dict[str, Any]]] = {}
            for d in docs:
                key = d.get("topic") or "(untopic)"
                by_topic.setdefault(key, []).append(d)
            for key in sorted(by_topic.keys(), key=lambda k: (-len(by_topic[k]), k)):
                groups.append({
                    "key": key,
                    "label": key.replace("-", " ").title() if key != "(untopic)" else "Untopic",
                    "count": len(by_topic[key]),
                    "docs": by_topic[key],
                })
        else:
            # view=stage — group by stage number
            by_stage: dict[int, list[dict[str, Any]]] = {}
            for d in docs:
                by_stage.setdefault(d["stage"] or 0, []).append(d)
            stage_labels = {
                0: "Reference / Indexes",
                1: "Captures",
                2: "Processed",
                3: "Research",
                4: "Synthesis",
                5: "Decisions",
                6: "Expertise",
            }
            for s in sorted(by_stage.keys()):
                groups.append({
                    "key": str(s),
                    "label": stage_labels.get(s, f"Stage {s}"),
                    "count": len(by_stage[s]),
                    "docs": by_stage[s],
                })

        return {"view": view, "groups": groups, "total": total}
    finally:
        conn.close()


def _inventory_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["issues"] = _parse_json(d.get("issues")) or []
    d["is_orphan"] = bool(d.get("is_orphan", 0))
    for k in ("has_summary", "has_concepts", "has_topic", "has_source_url"):
        d[k] = bool(d.get(k, 0))
    return d


@router.get("/library/file")
async def knowledge_library_file(path: str = Query(..., description="vault-relative path")) -> JSONResponse:
    """Read a vault file. Returns frontmatter + body as separate fields."""
    # Basic path safety — must be relative and under knowledge/
    if ".." in path or path.startswith("/"):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not path.startswith("knowledge/"):
        return JSONResponse({"error": "path must be under knowledge/"}, status_code=400)

    full = VAULT_DIR / path
    if not full.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)

    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)

    # Split frontmatter + body
    frontmatter: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            raw = content[3:end].strip()
            body = content[end + 4:].lstrip("\n")
            try:
                import yaml as _yaml
                fm = _yaml.safe_load(raw)
                if isinstance(fm, dict):
                    frontmatter = fm
            except Exception:
                pass

    stat = full.stat()
    return {
        "path": path,
        "frontmatter": frontmatter,
        "body": body,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# /api/knowledge/topics — list and view auto-maintained topic indexes
# ---------------------------------------------------------------------------

@router.get("/topics")
async def knowledge_topics(
    sort: str = Query("updated", description="updated | docs | alpha"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """List all topic indexes with doc counts and recent activity."""
    indexes_dir = VAULT_DIR / "knowledge" / "indexes"
    if not indexes_dir.is_dir():
        return {"topics": [], "total": 0}

    try:
        from engine.intelligence.topics import load_index
    except Exception:
        return {"topics": [], "total": 0}

    files = list(indexes_dir.glob("*.md"))
    topics: list[dict[str, Any]] = []
    for f in files:
        try:
            idx = load_index(f.stem)
            topics.append({
                "slug": idx.slug,
                "title": idx.title,
                "orientation": (idx.orientation or "").strip(),
                "doc_count": idx.doc_count,
                "captures_count": len(idx.captures),
                "research_count": len(idx.research),
                "synthesis_count": len(idx.synthesis),
                "decisions_count": len(idx.decisions),
                "expertise_count": len(idx.expertise),
                "open_questions": idx.open_questions,
                "updated": idx.updated,
                "file_mtime": datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "synthesis_suggested": (
                    len(idx.captures) >= 3 and not idx.research and not idx.synthesis
                ),
            })
        except Exception as e:
            logger.debug("load_index failed for %s: %s", f.name, e)
            continue

    # Sort
    if sort == "docs":
        topics.sort(key=lambda t: -t["doc_count"])
    elif sort == "alpha":
        topics.sort(key=lambda t: t["slug"])
    else:  # updated
        topics.sort(key=lambda t: t["file_mtime"], reverse=True)

    return {"topics": topics[:limit], "total": len(topics)}


@router.get("/topics/{slug}")
async def knowledge_topic_detail(slug: str) -> JSONResponse:
    """Full topic index including all captures/research/decisions lists."""
    try:
        from engine.intelligence.topics import load_index
    except Exception:
        return JSONResponse({"error": "topics module unavailable"}, status_code=500)

    try:
        idx = load_index(slug)
    except Exception as e:
        return JSONResponse({"error": f"load failed: {e}"}, status_code=500)

    if idx.doc_count == 0 and not idx.orientation:
        return JSONResponse({"error": "topic not found"}, status_code=404)

    def _entries(items):
        return [
            {
                "path": e.path,
                "title": e.title,
                "type": e.type,
                "stage": e.stage,
                "date": e.date,
                "summary": e.summary,
            }
            for e in items
        ]

    return {
        "slug": idx.slug,
        "title": idx.title,
        "orientation": idx.orientation,
        "updated": idx.updated,
        "doc_count": idx.doc_count,
        "captures": _entries(idx.captures),
        "research": _entries(idx.research),
        "synthesis": _entries(idx.synthesis),
        "decisions": _entries(idx.decisions),
        "expertise": _entries(idx.expertise),
        "open_questions": idx.open_questions,
    }


# ---------------------------------------------------------------------------
# /api/knowledge/pipeline — observability cockpit data
# ---------------------------------------------------------------------------

@router.get("/pipeline")
async def knowledge_pipeline() -> dict[str, Any]:
    """Observability cockpit data: cron runs, queue depth, LLM activity, source health.

    Note: this endpoint stitches together data from multiple tables. Part 9
    will add a dedicated cron_runs table with richer telemetry; for now we
    derive what we can from the existing schema.
    """
    if not DB_PATH.exists():
        return _empty_pipeline()

    conn = _get_db()
    try:
        return {
            "crons": _build_cron_rows(conn),
            "queues": _build_queues(conn),
            "sources": _build_source_health(conn),
            "llm_activity": _build_llm_activity(conn),
            "flow": _build_flow_stats(conn),
        }
    finally:
        conn.close()


def _empty_pipeline() -> dict[str, Any]:
    return {
        "crons": [],
        "queues": {"pending_extraction": 0, "pending_compilation": 0, "pending_review": 0},
        "sources": [],
        "llm_activity": [],
        "flow": {"sources": 0, "fetched_7d": 0, "stored_7d": 0, "compiled_7d": 0, "saved_7d": 0},
    }


def _build_cron_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Real cron health from the cron_runs telemetry table.

    Returns the latest run for each cron_name, plus aggregate stats from
    the last 24 hours (runs, success rate). Falls back to the timestamp
    heuristic for legacy crons that haven't been wrapped yet.
    """
    crons: list[dict[str, Any]] = []

    if _table_exists(conn, "cron_runs"):
        # Latest run per cron_name — use a window query
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT cron_name, MAX(started_at) as latest_started
                FROM cron_runs
                GROUP BY cron_name
            ),
            run_stats AS (
                SELECT
                    cron_name,
                    COUNT(*) as runs_24h,
                    SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_24h,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_24h,
                    AVG(duration_ms) as avg_duration_ms
                FROM cron_runs
                WHERE started_at >= datetime('now', '-24 hours')
                GROUP BY cron_name
            )
            SELECT
                cr.cron_name,
                cr.started_at,
                cr.ended_at,
                cr.duration_ms,
                cr.status,
                cr.exit_code,
                s.runs_24h,
                s.ok_24h,
                s.failed_24h,
                s.avg_duration_ms
            FROM latest l
            JOIN cron_runs cr ON cr.cron_name = l.cron_name AND cr.started_at = l.latest_started
            LEFT JOIN run_stats s ON s.cron_name = cr.cron_name
            ORDER BY cr.started_at DESC
            """
        ).fetchall()

        for r in rows:
            status = r["status"]
            # Stale = last run > 2× expected interval — we don't know the
            # expected interval here so just mark running or latest status.
            ui_status = "ok" if status == "ok" else "stale" if status == "failed" else "never_run"
            crons.append({
                "name": r["cron_name"],
                "schedule": "tracked",  # expected schedule is in crons.yaml, not here
                "last_run": r["started_at"],
                "last_duration_ms": r["duration_ms"],
                "last_status": r["status"],
                "last_exit_code": r["exit_code"],
                "runs_24h": r["runs_24h"] or 0,
                "ok_24h": r["ok_24h"] or 0,
                "failed_24h": r["failed_24h"] or 0,
                "avg_duration_ms": int(r["avg_duration_ms"]) if r["avg_duration_ms"] else None,
                "status": ui_status,
                "total_items": r["runs_24h"] or 0,
            })

    # If we have no real cron_runs data yet, fall back to the legacy
    # heuristic so the UI stays populated during the transition.
    if not crons:
        return _build_cron_rows_legacy(conn)

    return crons


def _build_cron_rows_legacy(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Legacy heuristic — infer cron state from downstream table timestamps.

    Used only when cron_runs is empty (first boot, or before any wrapped
    cron has run). Will be removed once every cron is wrapped.
    """
    crons: list[dict[str, Any]] = []

    if _table_exists(conn, "intelligence_briefs"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM intelligence_briefs"
        ).fetchone()
        crons.append({
            "name": "feed-ingest",
            "schedule": "every 30m",
            "last_run": row["latest"] if row else None,
            "total_items": row["cnt"] if row else 0,
            "status": "ok" if row and row["latest"] else "never_run",
        })

    if _table_exists(conn, "compilation_proposals"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM compilation_proposals"
        ).fetchone()
        crons.append({
            "name": "compile-capture",
            "schedule": "on demand",
            "last_run": row["latest"] if row else None,
            "total_items": row["cnt"] if row else 0,
            "status": "ok" if row and row["latest"] else "never_run",
        })

    if _table_exists(conn, "vault_inventory"):
        row = conn.execute(
            "SELECT MAX(last_scanned) as latest, COUNT(*) as cnt FROM vault_inventory"
        ).fetchone()
        crons.append({
            "name": "vault-inventory",
            "schedule": "every update cycle",
            "last_run": row["latest"] if row else None,
            "total_items": row["cnt"] if row else 0,
            "status": "ok" if row and row["latest"] else "never_run",
        })

    return crons


def _build_queues(conn: sqlite3.Connection) -> dict[str, int]:
    q = {"pending_extraction": 0, "pending_compilation": 0, "pending_review": 0, "orphans": 0}

    if _table_exists(conn, "intelligence_briefs"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM intelligence_briefs WHERE content_status = 'pending'"
        ).fetchone()
        q["pending_extraction"] = row["cnt"] if row else 0

    if _table_exists(conn, "compilation_proposals"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM compilation_proposals WHERE status = 'pending'"
        ).fetchone()
        q["pending_review"] = row["cnt"] if row else 0

    if _table_exists(conn, "vault_inventory"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM vault_inventory WHERE is_orphan = 1"
        ).fetchone()
        q["orphans"] = row["cnt"] if row else 0

    return q


def _build_source_health(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "intelligence_sources"):
        return []
    rows = conn.execute(
        """
        SELECT id, name, platform, is_active, last_checked, last_success,
               consecutive_failures, items_total
        FROM intelligence_sources
        ORDER BY is_active DESC, last_checked DESC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "platform": r["platform"],
            "is_active": bool(r["is_active"]),
            "last_checked": r["last_checked"],
            "last_success": r["last_success"],
            "consecutive_failures": r["consecutive_failures"] or 0,
            "items_total": r["items_total"] or 0,
            "healthy": bool(r["last_success"]) and (r["consecutive_failures"] or 0) == 0,
        }
        for r in rows
    ]


def _build_llm_activity(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Last 20 compilation passes with provenance."""
    if not _table_exists(conn, "compilation_proposals"):
        return []
    rows = conn.execute(
        """
        SELECT id, created_at, status, auto_accepted, topic_confidence,
               compilation_json
        FROM compilation_proposals
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()
    out = []
    for r in rows:
        comp = _parse_json(r["compilation_json"]) or {}
        out.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "status": r["status"],
            "auto_accepted": bool(r["auto_accepted"]),
            "topic_confidence": r["topic_confidence"],
            "topic": comp.get("topic", ""),
            "template": comp.get("template_used", ""),
            "model": comp.get("model", ""),
            "provider": comp.get("provider", ""),
            "tokens_in": comp.get("tokens_in", 0),
            "tokens_out": comp.get("tokens_out", 0),
            "duration_ms": comp.get("duration_ms", 0),
        })
    return out


def _build_flow_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Last-7-days Sankey-style flow: sources → fetched → stored → compiled → saved."""
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    flow = {"sources": 0, "fetched_7d": 0, "stored_7d": 0, "compiled_7d": 0, "saved_7d": 0}

    if _table_exists(conn, "intelligence_sources"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM intelligence_sources WHERE is_active = 1"
        ).fetchone()
        flow["sources"] = row["cnt"] if row else 0

    if _table_exists(conn, "intelligence_briefs"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM intelligence_briefs WHERE created_at >= ?",
            (seven_days_ago,),
        ).fetchone()
        flow["stored_7d"] = row["cnt"] if row else 0
        flow["fetched_7d"] = flow["stored_7d"]  # proxy; will be real in Part 9

        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM intelligence_briefs WHERE status = 'saved' AND created_at >= ?",
            (seven_days_ago,),
        ).fetchone()
        flow["saved_7d"] = row["cnt"] if row else 0

    if _table_exists(conn, "compilation_proposals"):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM compilation_proposals WHERE created_at >= ?",
            (seven_days_ago,),
        ).fetchone()
        flow["compiled_7d"] = row["cnt"] if row else 0

    return flow


# ---------------------------------------------------------------------------
# /api/knowledge/bootstrap — vault bootstrap flow (dry-run + execute)
# ---------------------------------------------------------------------------

from fastapi import Body


@router.get("/bootstrap/preview")
async def bootstrap_preview() -> dict[str, Any]:
    """Dry-run: walk inventory, show what would be processed and cost.

    Zero LLM calls, zero mutations. Safe to call at any time.
    """
    from engine.intelligence.bootstrap import build_preview
    preview = build_preview()
    return preview.to_dict()


@router.post("/bootstrap/start")
async def bootstrap_start(
    body: dict[str, Any] = Body(default_factory=dict),
) -> JSONResponse:
    """Start a new bootstrap run. Takes a git snapshot, kicks off worker.

    Request body (all optional):
        {
            "model": "haiku",
            "limit": 10   // cap to first N eligible docs for testing
        }
    """
    from engine.intelligence.bootstrap import start_run

    model = body.get("model", "haiku")
    limit = body.get("limit")

    try:
        run = start_run(model=model, limit=limit)
    except RuntimeError as e:
        return JSONResponse(
            {"error": str(e)}, status_code=409,
        )
    except Exception as e:
        logger.exception("bootstrap_start failed")
        return JSONResponse(
            {"error": f"Start failed: {e}"}, status_code=500,
        )

    return run.to_dict()


@router.get("/bootstrap/runs")
async def bootstrap_list_runs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """List recent bootstrap runs."""
    from engine.intelligence.bootstrap import list_runs
    runs = list_runs(limit=limit)
    return {"runs": [r.to_dict() for r in runs]}


@router.get("/bootstrap/runs/{run_id}")
async def bootstrap_get_run(run_id: str) -> JSONResponse:
    """Live status of one bootstrap run."""
    from engine.intelligence.bootstrap import get_run
    run = get_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return run.to_dict()


@router.post("/bootstrap/runs/{run_id}/pause")
async def bootstrap_pause(run_id: str) -> JSONResponse:
    from engine.intelligence.bootstrap import pause_run
    run = pause_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return run.to_dict()


@router.post("/bootstrap/runs/{run_id}/resume")
async def bootstrap_resume(run_id: str) -> JSONResponse:
    from engine.intelligence.bootstrap import resume_run
    run = resume_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return run.to_dict()


@router.post("/bootstrap/runs/{run_id}/cancel")
async def bootstrap_cancel(run_id: str) -> JSONResponse:
    from engine.intelligence.bootstrap import cancel_run
    run = cancel_run(run_id)
    if run is None:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return run.to_dict()


# ---------------------------------------------------------------------------
# /api/knowledge/maintenance — nightly vault maintenance reports
# ---------------------------------------------------------------------------

@router.get("/maintenance")
async def knowledge_maintenance(limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    """List recent vault maintenance reports.

    Reports are written by the `vault-maintenance` cron to
    ~/vault/log/YYYY-MM-DD-maintenance.md. This endpoint returns a
    summary of each one (from its frontmatter) so the UI can show a
    trend + link to the latest report's body.
    """
    try:
        from engine.intelligence.lint.report import list_reports, find_latest_report
    except ImportError:
        from core.engine.intelligence.lint.report import list_reports, find_latest_report

    reports = list_reports(limit=limit)
    latest_path = find_latest_report()
    latest_body: str | None = None
    if latest_path is not None and latest_path.is_file():
        try:
            latest_body = latest_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read latest maintenance report: %s", e)

    return {
        "reports": reports,
        "latest_path": str(latest_path.relative_to(VAULT_DIR)) if latest_path else None,
        "latest_body": latest_body,
    }


@router.post("/maintenance/run")
async def knowledge_maintenance_run(
    body: dict[str, Any] = Body(default_factory=dict),
) -> JSONResponse:
    """Manually trigger a maintenance pass (operator-initiated).

    Request body (optional):
        { "skip_llm": true, "max_topics": 5, "max_synthesis": 3 }

    Normally the cron runs this nightly at 04:30. This endpoint lets the
    operator kick off an immediate run from the Knowledge UI.
    """
    try:
        from engine.intelligence.lint import run_maintenance_pass
    except ImportError:
        from core.engine.intelligence.lint import run_maintenance_pass

    try:
        report = await run_maintenance_pass(
            skip_llm=body.get("skip_llm"),
            model=body.get("model"),
            max_topics=body.get("max_topics"),
            max_synthesis=body.get("max_synthesis"),
        )
        return report.to_dict()
    except Exception as e:
        logger.exception("Manual maintenance run failed")
        return JSONResponse(
            {"error": f"Maintenance pass failed: {e}"}, status_code=500,
        )


# ---------------------------------------------------------------------------
# /api/knowledge/health — single-line status for the header strip
# ---------------------------------------------------------------------------

@router.get("/health")
async def knowledge_health() -> dict[str, Any]:
    """Cheap endpoint for the header status strip. Called frequently."""
    if not DB_PATH.exists():
        return {"healthy": False, "summary": "System offline"}

    conn = _get_db()
    try:
        return _build_status(conn, datetime.now(timezone.utc))
    finally:
        conn.close()
