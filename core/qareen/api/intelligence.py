"""Qareen API -- Intelligence feed routes.

List, search, save, and manage intelligence items and watched sources.
Reads from intelligence_sources and intelligence_briefs tables in qareen.db.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
VAULT_DIR = Path.home() / "vault"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _parse_json_field(value: Any) -> Any:
    """Safely parse a JSON text field into a Python object."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _brief_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a brief row to a response dict, parsing JSON fields."""
    d = dict(row)
    d["key_findings"] = _parse_json_field(d.get("key_findings"))
    d["relevance_tags"] = _parse_json_field(d.get("relevance_tags"))
    d["raw_data"] = _parse_json_field(d.get("raw_data"))
    d["surfaced"] = bool(d.get("surfaced", 0))
    return d


def _source_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a source row to a response dict, parsing JSON fields."""
    d = dict(row)
    d["keywords"] = _parse_json_field(d.get("keywords"))
    d["config"] = _parse_json_field(d.get("config"))
    d["is_active"] = bool(d.get("is_active", 1))
    return d


def _slugify(text: str) -> str:
    """Create a filename-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:40] if text else "unknown"


# ---------------------------------------------------------------------------
# Feed endpoints
# ---------------------------------------------------------------------------

@router.get("/feed")
async def list_feed(
    request: Request,
    days: int = Query(30, description="How many days back to look", ge=1, le=365),
    limit: int = Query(50, description="Max items to return", ge=1, le=200),
    platform: str | None = Query(None, description="Filter by platform"),
    status: str | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search title/summary"),
) -> dict[str, Any]:
    """List intelligence feed items, newest first."""
    if not DB_PATH.exists():
        return {"items": [], "total": 0}

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_briefs"):
            return {"items": [], "total": 0}

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conditions = ["b.created_at >= ?"]
        params: list[Any] = [cutoff]

        if platform:
            conditions.append("b.platform = ?")
            params.append(platform)

        if status:
            conditions.append("b.status = ?")
            params.append(status)

        if search:
            conditions.append("(b.title LIKE ? OR b.summary LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])

        where = " AND ".join(conditions)

        # Get total count
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM intelligence_briefs b WHERE {where}",
            params,
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # Get items
        rows = conn.execute(
            f"""
            SELECT b.*, s.name as source_name
            FROM intelligence_briefs b
            LEFT JOIN intelligence_sources s ON b.source_id = s.id
            WHERE {where}
            ORDER BY b.published_at DESC, b.created_at DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()

        items = []
        for row in rows:
            d = _brief_to_dict(row)
            d["source_name"] = row["source_name"]
            items.append(d)

        return {"items": items, "total": total}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Item endpoints
# ---------------------------------------------------------------------------

@router.get("/items/{item_id}")
async def get_item(item_id: str) -> dict[str, Any] | JSONResponse:
    """Get a single intelligence item with full content."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_briefs"):
            return JSONResponse({"error": "Item not found"}, status_code=404)

        row = conn.execute(
            """
            SELECT b.*, s.name as source_name
            FROM intelligence_briefs b
            LEFT JOIN intelligence_sources s ON b.source_id = s.id
            WHERE b.id = ?
            """,
            (item_id,),
        ).fetchone()

        if not row:
            return JSONResponse({"error": "Item not found"}, status_code=404)

        d = _brief_to_dict(row)
        d["source_name"] = row["source_name"]
        return d
    finally:
        conn.close()


@router.post("/items/{item_id}/save")
async def save_item(item_id: str) -> dict[str, Any] | JSONResponse:
    """Promote an intelligence item to a vault capture.

    Generates a markdown file in ~/vault/knowledge/captures/ with proper
    frontmatter and updates the DB record with vault_path and status='saved'.
    """
    if not DB_PATH.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_briefs"):
            return JSONResponse({"error": "Item not found"}, status_code=404)

        row = conn.execute(
            "SELECT * FROM intelligence_briefs WHERE id = ?", (item_id,),
        ).fetchone()

        if not row:
            return JSONResponse({"error": "Item not found"}, status_code=404)

        brief = _brief_to_dict(row)

        # Check if already saved
        if brief.get("vault_path"):
            return {"status": "already_saved", "vault_path": brief["vault_path"]}

        # Build vault capture filename
        now = datetime.now()
        date_prefix = now.strftime("%Y%m%d")
        platform = brief.get("platform") or "web"
        author = _slugify(brief.get("author") or "unknown")
        short_id = item_id[:8]
        filename = f"{date_prefix}-{platform}-{author}-{short_id}.md"

        captures_dir = VAULT_DIR / "knowledge" / "captures"
        captures_dir.mkdir(parents=True, exist_ok=True)
        vault_path = captures_dir / filename

        # Build tags
        tags = []
        if brief.get("platform"):
            tags.append(brief["platform"])
        if brief.get("category"):
            tags.append(brief["category"])
        relevance_tags = brief.get("relevance_tags") or []
        if isinstance(relevance_tags, list):
            tags.extend(relevance_tags[:5])

        # Build frontmatter
        safe_title = brief.get("title", "Untitled").replace('"', '\\"')
        safe_author = (brief.get("author") or "").replace('"', '\\"')
        fm_lines = [
            "---",
            f'title: "{safe_title}"',
            "type: capture",
            "stage: 1",
            f"date: {now.strftime('%Y-%m-%d')}",
            f"source_url: \"{brief.get('url', '')}\"",
            f'author: "{safe_author}"',
            f"platform: {platform}",
            f"tags: [{', '.join(tags)}]",
            f"captured_from: intelligence-feed",
            f"intelligence_id: {item_id}",
        ]
        if brief.get("relevance_score"):
            fm_lines.append(f"relevance_score: {brief['relevance_score']}")
        if brief.get("project_id"):
            fm_lines.append(f"project: {brief['project_id']}")
        fm_lines.append("---")

        # Build body
        body_parts = []
        if brief.get("summary"):
            body_parts.append(brief["summary"])
            body_parts.append("")

        key_findings = brief.get("key_findings")
        if key_findings and isinstance(key_findings, list):
            body_parts.append("## Key Findings")
            for finding in key_findings:
                body_parts.append(f"- {finding}")
            body_parts.append("")

        if brief.get("content"):
            body_parts.append("## Content")
            body_parts.append(brief["content"])

        if brief.get("url"):
            body_parts.append("")
            body_parts.append(f"**Source:** {brief['url']}")

        markdown = "\n".join(fm_lines) + "\n\n" + "\n".join(body_parts) + "\n"

        # Write file
        vault_path.write_text(markdown, encoding="utf-8")

        # Compute relative path for DB storage
        relative_vault = str(vault_path.relative_to(VAULT_DIR))

        # Update DB
        conn.execute(
            """
            UPDATE intelligence_briefs
            SET status = 'saved', vault_path = ?, operator_action = 'saved'
            WHERE id = ?
            """,
            (relative_vault, item_id),
        )
        conn.commit()

        return {
            "status": "saved",
            "vault_path": relative_vault,
            "filename": filename,
        }
    finally:
        conn.close()


@router.post("/items/{item_id}/dismiss")
async def dismiss_item(item_id: str) -> dict[str, Any] | JSONResponse:
    """Mark an intelligence item as dismissed."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_briefs"):
            return JSONResponse({"error": "Item not found"}, status_code=404)

        row = conn.execute(
            "SELECT id FROM intelligence_briefs WHERE id = ?", (item_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Item not found"}, status_code=404)

        conn.execute(
            """
            UPDATE intelligence_briefs
            SET status = 'dismissed', operator_action = 'dismissed'
            WHERE id = ?
            """,
            (item_id,),
        )
        conn.commit()
        return {"status": "dismissed", "id": item_id}
    finally:
        conn.close()


@router.post("/items/{item_id}/read")
async def mark_read(item_id: str) -> dict[str, Any] | JSONResponse:
    """Mark an intelligence item as read."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_briefs"):
            return JSONResponse({"error": "Item not found"}, status_code=404)

        row = conn.execute(
            "SELECT id FROM intelligence_briefs WHERE id = ?", (item_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Item not found"}, status_code=404)

        conn.execute(
            """
            UPDATE intelligence_briefs
            SET status = 'read', surfaced = 1, surfaced_at = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), item_id),
        )
        conn.commit()
        return {"status": "read", "id": item_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Source endpoints
# ---------------------------------------------------------------------------

@router.get("/sources")
async def list_sources() -> dict[str, Any]:
    """List all watched intelligence sources."""
    if not DB_PATH.exists():
        return {"sources": []}

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_sources"):
            return {"sources": []}

        rows = conn.execute(
            "SELECT * FROM intelligence_sources ORDER BY name",
        ).fetchall()

        return {"sources": [_source_to_dict(row) for row in rows]}
    finally:
        conn.close()


@router.post("/sources")
async def create_source(request: Request) -> dict[str, Any] | JSONResponse:
    """Add a new intelligence source."""
    body = await request.json()

    name = body.get("name")
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    platform = body.get("platform")
    route = body.get("route")
    route_url = body.get("route_url")
    priority = body.get("priority", "normal")
    keywords = body.get("keywords")
    url = body.get("url")
    category = body.get("category")
    update_cadence = body.get("update_cadence", "hourly")
    layer = body.get("layer", 5)
    tier = body.get("tier", "social")
    config = body.get("config")
    project_id = body.get("project_id")

    source_id = uuid.uuid4().hex[:12]

    # Ensure DB directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = _get_db()
    try:
        # Table should already exist from schema migration, but be defensive
        if not _table_exists(conn, "intelligence_sources"):
            return JSONResponse(
                {"error": "Intelligence tables not initialized"},
                status_code=500,
            )

        conn.execute(
            """
            INSERT INTO intelligence_sources
                (id, name, platform, layer, tier, url, route, route_url,
                 priority, keywords, update_cadence, is_active, category,
                 project_id, config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                source_id,
                name,
                platform,
                layer,
                tier,
                url,
                route,
                route_url,
                priority,
                json.dumps(keywords) if keywords else None,
                update_cadence,
                category,
                project_id,
                json.dumps(config) if config else None,
            ),
        )
        conn.commit()

        # Fetch back the created row
        row = conn.execute(
            "SELECT * FROM intelligence_sources WHERE id = ?", (source_id,),
        ).fetchone()

        return _source_to_dict(row)
    finally:
        conn.close()


@router.put("/sources/{source_id}")
async def update_source(
    source_id: str, request: Request,
) -> dict[str, Any] | JSONResponse:
    """Update an existing intelligence source."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Source not found"}, status_code=404)

    body = await request.json()

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_sources"):
            return JSONResponse({"error": "Source not found"}, status_code=404)

        row = conn.execute(
            "SELECT id FROM intelligence_sources WHERE id = ?", (source_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Source not found"}, status_code=404)

        # Build dynamic SET clause from provided fields
        allowed = {
            "name", "platform", "layer", "tier", "url", "route", "route_url",
            "priority", "keywords", "update_cadence", "is_active", "category",
            "project_id", "config",
        }
        sets = []
        params: list[Any] = []
        for field, value in body.items():
            if field not in allowed:
                continue
            # JSON-encode list/dict fields
            if field in ("keywords", "config") and value is not None:
                value = json.dumps(value)
            sets.append(f"{field} = ?")
            params.append(value)

        if not sets:
            return JSONResponse(
                {"error": "No valid fields to update"}, status_code=400,
            )

        params.append(source_id)
        conn.execute(
            f"UPDATE intelligence_sources SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()

        # Return updated record
        row = conn.execute(
            "SELECT * FROM intelligence_sources WHERE id = ?", (source_id,),
        ).fetchone()
        return _source_to_dict(row)
    finally:
        conn.close()


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str) -> dict[str, Any] | JSONResponse:
    """Remove an intelligence source."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Source not found"}, status_code=404)

    conn = _get_db()
    try:
        if not _table_exists(conn, "intelligence_sources"):
            return JSONResponse({"error": "Source not found"}, status_code=404)

        row = conn.execute(
            "SELECT id FROM intelligence_sources WHERE id = ?", (source_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Source not found"}, status_code=404)

        # Delete related briefs first (FK constraint)
        conn.execute(
            "DELETE FROM intelligence_briefs WHERE source_id = ?", (source_id,),
        )
        conn.execute(
            "DELETE FROM intelligence_sources WHERE id = ?", (source_id,),
        )
        conn.commit()
        return {"status": "deleted", "id": source_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Summary stats for the intelligence system."""
    if not DB_PATH.exists():
        return {
            "total_items": 0,
            "unread_count": 0,
            "sources_count": 0,
            "active_sources": 0,
            "last_ingest": None,
            "platforms": {},
        }

    conn = _get_db()
    try:
        briefs_exist = _table_exists(conn, "intelligence_briefs")
        sources_exist = _table_exists(conn, "intelligence_sources")

        total_items = 0
        unread_count = 0
        last_ingest = None
        platforms: dict[str, int] = {}

        if briefs_exist:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM intelligence_briefs",
            ).fetchone()
            total_items = row["cnt"] if row else 0

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM intelligence_briefs WHERE status = 'unread'",
            ).fetchone()
            unread_count = row["cnt"] if row else 0

            row = conn.execute(
                "SELECT MAX(created_at) as latest FROM intelligence_briefs",
            ).fetchone()
            last_ingest = row["latest"] if row else None

            # Platform breakdown
            prows = conn.execute(
                """
                SELECT platform, COUNT(*) as cnt
                FROM intelligence_briefs
                WHERE platform IS NOT NULL
                GROUP BY platform
                """,
            ).fetchall()
            platforms = {r["platform"]: r["cnt"] for r in prows}

        sources_count = 0
        active_sources = 0
        if sources_exist:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM intelligence_sources",
            ).fetchone()
            sources_count = row["cnt"] if row else 0

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM intelligence_sources WHERE is_active = 1",
            ).fetchone()
            active_sources = row["cnt"] if row else 0

        return {
            "total_items": total_items,
            "unread_count": unread_count,
            "sources_count": sources_count,
            "active_sources": active_sources,
            "last_ingest": last_ingest,
            "platforms": platforms,
        }
    finally:
        conn.close()
