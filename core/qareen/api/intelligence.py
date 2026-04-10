"""Qareen API -- Intelligence feed routes.

List, search, save, and manage intelligence items and watched sources.
Reads from intelligence_sources and intelligence_briefs tables in qareen.db.
"""

from __future__ import annotations

import asyncio
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

# Shadow-mode threshold: proposals with topic_confidence >= this value
# auto-accept straight through to the vault. Lower-confidence proposals
# wait for operator approval. Configurable via environment variable.
import os as _os
SHADOW_ACCEPT_THRESHOLD = float(_os.environ.get("AOS_SHADOW_THRESHOLD", "0.85"))


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
async def get_item(item_id: str) -> JSONResponse:
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


@router.post("/items/{item_id}/extract")
async def extract_item(item_id: str) -> JSONResponse:
    """Fetch full content for an intelligence item on demand.

    This is the deferred-extraction path. Ingest stores RSS metadata with
    content_status='pending'. When the operator opens an item in the UI,
    the frontend hits this endpoint to pull the full content.

    Idempotent: if the item is already 'extracted' or 'extracting', returns
    immediately with the current state.
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
        url = brief.get("url")
        status = brief.get("content_status") or "pending"

        # Already done — return as-is
        if status == "extracted" and brief.get("content"):
            return {"status": "extracted", "cached": True, "item": brief}

        # In flight from another caller — tell the client to poll
        if status == "extracting":
            return {"status": "extracting", "cached": False, "item": brief}

        if not url:
            conn.execute(
                "UPDATE intelligence_briefs SET content_status='failed' WHERE id=?",
                (item_id,),
            )
            conn.commit()
            return JSONResponse(
                {"error": "Item has no URL to extract from"}, status_code=400,
            )

        # Mark in-flight
        conn.execute(
            "UPDATE intelligence_briefs SET content_status='extracting' WHERE id=?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Run the actual extraction outside the DB lock via the content router
    try:
        from engine.intelligence.content import router
        from engine.intelligence.content.result import ExtractionError
        result = await router.extract(url)
    except ExtractionError as e:
        logger.warning("Extraction failed for %s: %s", item_id, e)
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE intelligence_briefs SET content_status='failed' WHERE id=?",
                (item_id,),
            )
            conn.commit()
        finally:
            conn.close()
        return JSONResponse(
            {"error": f"Extraction failed: {e}", "status": "failed",
             "backend": e.backend},
            status_code=502,
        )
    except Exception as e:
        logger.exception("Extraction crashed for %s", item_id)
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE intelligence_briefs SET content_status='failed' WHERE id=?",
                (item_id,),
            )
            conn.commit()
        finally:
            conn.close()
        return JSONResponse(
            {"error": f"Extraction crashed: {e}", "status": "failed"},
            status_code=500,
        )

    # Write result back
    conn = _get_db()
    try:
        if not result.has_content():
            conn.execute(
                "UPDATE intelligence_briefs SET content_status='failed' WHERE id=?",
                (item_id,),
            )
            conn.commit()
            return JSONResponse(
                {"error": "Extractor returned no content", "status": "failed",
                 "backend": result.backend},
                status_code=502,
            )

        content = result.content
        new_title = result.title
        new_author = result.author

        updates = ["content = ?", "content_status = 'extracted'"]
        params: list[Any] = [content]

        # Upgrade title/author if extractor produced better values
        if new_title and len(new_title) > len(brief.get("title") or ""):
            updates.append("title = ?")
            params.append(new_title)
        if new_author and not brief.get("author"):
            updates.append("author = ?")
            params.append(new_author)

        params.append(item_id)
        conn.execute(
            f"UPDATE intelligence_briefs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

        # Return fresh row
        fresh = conn.execute(
            """
            SELECT b.*, s.name as source_name
            FROM intelligence_briefs b
            LEFT JOIN intelligence_sources s ON b.source_id = s.id
            WHERE b.id = ?
            """,
            (item_id,),
        ).fetchone()
        d = _brief_to_dict(fresh)
        d["source_name"] = fresh["source_name"]
        return {"status": "extracted", "cached": False, "item": d}
    finally:
        conn.close()


@router.post("/items/{item_id}/save")
async def save_item(item_id: str) -> JSONResponse:
    """Promote an intelligence item to a compiled vault capture.

    Shadow-mode pipeline:
        1. Load brief from DB
        2. If not yet extracted → run the content router
        3. Run the Haiku compilation pass (Pass 2)
        4. Write a compilation_proposal row (always)
        5. If topic_confidence >= SHADOW_ACCEPT_THRESHOLD:
            → auto-accept: apply to vault (file + index + links + DB)
            → return status='saved', auto_accepted=True, proposal_id
           Else:
            → leave proposal pending
            → return status='proposal_pending', proposal_id
            → the operator can approve or reject via /proposals/{id}/approve|reject

    Returns a payload rich enough for the UI to show the result either
    way, with cost/timing breadcrumbs and topic/entity/concept details.
    """
    if not DB_PATH.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    # ------------------------------------------------------------------
    # Step 1: Load brief
    # ------------------------------------------------------------------
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
        if brief.get("vault_path"):
            return {
                "status": "already_saved",
                "vault_path": brief["vault_path"],
            }

        content_status = brief.get("content_status") or "pending"
        url = brief.get("url") or ""
        platform = brief.get("platform") or ""
    finally:
        conn.close()

    if not url:
        return JSONResponse(
            {"error": "Item has no URL, cannot compile"}, status_code=400,
        )

    # ------------------------------------------------------------------
    # Step 2: Ensure we have extracted content
    # ------------------------------------------------------------------
    from engine.intelligence.content import router as content_router
    from engine.intelligence.content.result import (
        ExtractionError,
        ExtractionResult,
    )

    extraction: ExtractionResult
    try:
        if content_status == "extracted" and brief.get("content"):
            # Reconstruct a minimal ExtractionResult from the DB row —
            # we already have the content, no need to re-fetch.
            extraction = ExtractionResult(
                url=url,
                platform=platform,
                title=brief.get("title") or "",
                author=brief.get("author") or "",
                content=brief.get("content") or "",
                published_at=brief.get("published_at"),
                media=[],
                links=[],
                metadata={},
                backend="db_cache",
            )
        else:
            # Fresh extraction via the router
            extraction = await content_router.extract(url)
    except ExtractionError as e:
        logger.warning("Save failed at extract step for %s: %s", item_id, e)
        return JSONResponse(
            {"error": f"Extraction failed: {e}", "backend": e.backend},
            status_code=502,
        )

    # ------------------------------------------------------------------
    # Step 3: Compile via Haiku (Pass 2)
    # ------------------------------------------------------------------
    from engine.intelligence.compile import (
        CompilationError,
        compile_capture,
    )

    try:
        compilation = await compile_capture(extraction)
    except CompilationError as e:
        logger.warning("Save failed at compile step for %s: %s", item_id, e)
        return JSONResponse(
            {"error": f"Compilation failed: {e}"}, status_code=502,
        )

    # ------------------------------------------------------------------
    # Step 4: Write a compilation proposal row
    # ------------------------------------------------------------------
    proposal_id = _write_proposal(
        source="intelligence_brief",
        source_id=item_id,
        extraction=extraction,
        compilation=compilation,
    )

    # ------------------------------------------------------------------
    # Step 5: Decide — auto-accept or leave pending
    # ------------------------------------------------------------------
    if compilation.topic_confidence >= SHADOW_ACCEPT_THRESHOLD:
        # Auto-accept path — apply to vault now
        try:
            applied = _apply_compilation_to_vault(
                item_id=item_id,
                extraction=extraction,
                compilation=compilation,
                relevance_score=brief.get("relevance_score"),
            )
        except Exception as e:
            logger.exception("Auto-accept vault apply failed for %s", item_id)
            _mark_proposal(
                proposal_id,
                status="pending",
                reviewed_by=None,
                reject_reason=f"auto_accept_apply_error: {e}",
            )
            return JSONResponse(
                {"error": f"Vault apply failed: {e}", "proposal_id": proposal_id},
                status_code=500,
            )

        _mark_proposal(
            proposal_id,
            status="auto_accepted",
            auto_accepted=True,
            vault_path=applied["vault_path"],
            reviewed_by="auto",
        )

        # Fire automation hooks — best effort, never blocks
        try:
            try:
                from engine.intelligence.hooks import emit_brief_compiled, emit_brief_created
            except ImportError:
                from core.engine.intelligence.hooks import emit_brief_compiled, emit_brief_created
            await emit_brief_created(brief)
            await emit_brief_compiled(brief, compilation.to_dict())
        except Exception:
            logger.debug("hook emit failed (non-fatal)", exc_info=True)

        return {
            "status": "saved",
            "proposal_id": proposal_id,
            "auto_accepted": True,
            "vault_path": applied["vault_path"],
            "filename": applied["filename"],
            "topic": {
                "slug": compilation.topic,
                "confidence": compilation.topic_confidence,
                "is_new": compilation.topic_is_new,
                "index_path": applied["topic_index_path"],
            },
            "concepts": compilation.concepts,
            "entities": compilation.entities,
            "summary": compilation.summary,
            "links_created": applied["links_created"],
            "compilation": {
                "model": compilation.model,
                "provider": compilation.provider,
                "duration_ms": compilation.duration_ms,
                "template": compilation.template_used,
                "tokens_in": compilation.tokens_in,
                "tokens_out": compilation.tokens_out,
            },
        }

    # Proposal pending path — don't touch vault; operator must approve.
    # Fire the proposal_pending hook so the Pipeline view badge refreshes.
    try:
        try:
            from engine.intelligence.hooks import emit_proposal_pending
        except ImportError:
            from core.engine.intelligence.hooks import emit_proposal_pending
        await emit_proposal_pending({
            "proposal_id": proposal_id,
            "title": extraction.title,
            "topic": compilation.topic,
            "confidence": compilation.topic_confidence,
        })
    except Exception:
        logger.debug("proposal_pending hook emit failed", exc_info=True)

    return {
        "status": "proposal_pending",
        "proposal_id": proposal_id,
        "auto_accepted": False,
        "threshold": SHADOW_ACCEPT_THRESHOLD,
        "topic": {
            "slug": compilation.topic,
            "confidence": compilation.topic_confidence,
            "is_new": compilation.topic_is_new,
        },
        "concepts": compilation.concepts,
        "entities": compilation.entities,
        "summary": compilation.summary,
        "reason": (
            f"topic_confidence={compilation.topic_confidence:.2f} < "
            f"threshold={SHADOW_ACCEPT_THRESHOLD:.2f} — awaiting operator review"
        ),
        "compilation": {
            "model": compilation.model,
            "provider": compilation.provider,
            "duration_ms": compilation.duration_ms,
            "template": compilation.template_used,
        },
    }


# ---------------------------------------------------------------------------
# Shadow-mode helpers
# ---------------------------------------------------------------------------

def _topic_title_from_slug(slug: str) -> str:
    """Convert 'platform-exodus' → 'Platform Exodus' for index title display."""
    if not slug:
        return "Uncategorized"
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-"))


def _write_proposal(
    *,
    source: str,
    source_id: str,
    extraction: Any,
    compilation: Any,
) -> str:
    """Persist a compilation_proposal row. Returns the proposal id."""
    proposal_id = uuid.uuid4().hex[:12]
    now_iso = datetime.now().isoformat()
    extraction_json = json.dumps(extraction.to_dict(), default=str)
    compilation_json = json.dumps(compilation.to_dict(), default=str)

    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO compilation_proposals
                (id, created_at, source, source_id, status, auto_accepted,
                 topic_confidence, extraction_json, compilation_json)
            VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (
                proposal_id,
                now_iso,
                source,
                source_id,
                float(compilation.topic_confidence or 0.0),
                extraction_json,
                compilation_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return proposal_id


def _mark_proposal(
    proposal_id: str,
    *,
    status: str,
    auto_accepted: bool | None = None,
    vault_path: str | None = None,
    reviewed_by: str | None = None,
    reject_reason: str | None = None,
) -> None:
    """Update the status/metadata of a compilation_proposal row."""
    conn = _get_db()
    try:
        sets = ["status = ?"]
        params: list[Any] = [status]
        if auto_accepted is not None:
            sets.append("auto_accepted = ?")
            params.append(1 if auto_accepted else 0)
        if vault_path is not None:
            sets.append("vault_path = ?")
            params.append(vault_path)
        if reviewed_by is not None:
            sets.append("reviewed_by = ?")
            params.append(reviewed_by)
            sets.append("reviewed_at = ?")
            params.append(datetime.now().isoformat())
        if reject_reason is not None:
            sets.append("reject_reason = ?")
            params.append(reject_reason)
        params.append(proposal_id)
        conn.execute(
            f"UPDATE compilation_proposals SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def _apply_compilation_to_vault(
    *,
    item_id: str,
    extraction: Any,
    compilation: Any,
    relevance_score: float | None,
) -> dict[str, Any]:
    """Write capture file, update topic index, create ontology links, update briefs DB.

    Called both by the auto-accept path in save_item and by the manual
    approve endpoint. Returns {vault_path, filename, topic_index_path,
    links_created}.
    """
    from engine.intelligence.compile.templates import get_template
    from engine.intelligence.topics import TopicEntry, update_index, slugify as topic_slugify
    import yaml as _yaml

    template = get_template(extraction.platform)
    frontmatter = template.build_frontmatter(
        extraction=extraction,
        compilation=compilation.to_dict(),
        intelligence_id=item_id,
        relevance_score=relevance_score,
    )
    # Enforce the template's declared mandatory-field contract. build_frontmatter
    # populates the base spec by construction, but a per-platform template that
    # adds a mandatory field without populating it would otherwise drift into the
    # vault silently — surface it here instead.
    missing = template.validate_frontmatter(frontmatter)
    if missing:
        logger.warning(
            "capture %s (%s template) missing mandatory frontmatter fields: %s",
            item_id, template.name, ", ".join(missing),
        )
    body = template.body(
        extraction=extraction,
        compilation=compilation.to_dict(),
    )

    now = datetime.now()
    date_prefix = now.strftime("%Y%m%d")
    slug = _slugify(extraction.title or (extraction.url or "").split("/")[-1] or "untitled")
    short_id = item_id[:8]
    filename = f"{date_prefix}-{template.name}-{slug}-{short_id}.md"

    captures_dir = VAULT_DIR / "knowledge" / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    capture_path = captures_dir / filename

    markdown = (
        "---\n"
        + _yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + body
    )
    capture_path.write_text(markdown, encoding="utf-8")
    relative_vault_path = capture_path.relative_to(VAULT_DIR).as_posix()

    # Topic index
    topic_index_path: str | None = None
    topic_slug = compilation.topic or "uncategorized"
    try:
        entry = TopicEntry(
            path=relative_vault_path,
            title=extraction.title or "Untitled",
            type="capture",
            stage=int(frontmatter.get("stage", 1)),
            date=frontmatter.get("date", now.strftime("%Y-%m-%d")),
            summary=compilation.summary or "",
        )
        idx_path = update_index(
            slug=topic_slugify(topic_slug),
            title=_topic_title_from_slug(topic_slug),
            entry=entry,
        )
        topic_index_path = str(idx_path.relative_to(VAULT_DIR)) if idx_path else None
    except Exception as e:
        logger.warning("Topic index update failed for %s: %s", item_id, e)

    # Ontology links
    links_created = _create_entity_links(
        capture_id=item_id,
        entities=compilation.entities,
    )

    # DB update
    conn = _get_db()
    try:
        conn.execute(
            """
            UPDATE intelligence_briefs
            SET status = 'saved',
                vault_path = ?,
                content = COALESCE(NULLIF(?, ''), content),
                content_status = CASE WHEN LENGTH(?) > 0 THEN 'extracted' ELSE content_status END,
                operator_action = 'saved'
            WHERE id = ?
            """,
            (
                relative_vault_path,
                extraction.content or "",
                extraction.content or "",
                item_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "vault_path": relative_vault_path,
        "filename": filename,
        "topic_index_path": topic_index_path,
        "links_created": links_created,
    }


def _create_entity_links(*, capture_id: str, entities: list[dict]) -> int:
    """Create MENTIONS links from CAPTURE to matching PERSON rows.

    Skips non-person entities, low-confidence LLM outputs, and weak name
    matches. Returns the count of links actually written. Never raises —
    linking failures don't block the save.
    """
    if not entities:
        return 0
    count = 0
    try:
        from core.qareen.ontology.types import LinkType, ObjectType
        from core.qareen.ontology.adapters.intelligence import IntelligenceAdapter
        from core.qareen.ontology.adapters.people import PeopleAdapter

        intel_adapter = IntelligenceAdapter(
            vault_dir=str(VAULT_DIR),
            qareen_db_path=str(DB_PATH),
        )
        people_adapter = PeopleAdapter(
            people_db_path=str(Path.home() / ".aos" / "data" / "people.db"),
            qareen_db_path=str(DB_PATH),
        )

        for entity in entities:
            if entity.get("type") != "person":
                continue
            name = (entity.get("name") or "").strip()
            llm_conf = float(entity.get("confidence") or 0.0)
            if not name or llm_conf < 0.7:
                continue
            try:
                matches = people_adapter.search(name, limit=1)
            except Exception:
                matches = []
            if not matches or matches[0].score < 0.6:
                continue
            top = matches[0]
            try:
                intel_adapter.create_link(
                    source_id=capture_id,
                    target_type=ObjectType.PERSON,
                    target_id=top.object_id,
                    link_type=LinkType.MENTIONS,
                    metadata={
                        "source": "compile_pass_2",
                        "llm_confidence": llm_conf,
                        "name_match_score": top.score,
                        "entity_name": name,
                    },
                )
                count += 1
            except Exception as e:
                logger.warning("Failed to create link for entity %s: %s", name, e)
    except Exception as e:
        logger.warning("Entity link step failed: %s", e)
    return count


# ---------------------------------------------------------------------------
# Compilation proposal endpoints (shadow-mode review)
# ---------------------------------------------------------------------------

def _proposal_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["extraction"] = _parse_json_field(d.pop("extraction_json", None))
    d["compilation"] = _parse_json_field(d.pop("compilation_json", None))
    d["auto_accepted"] = bool(d.get("auto_accepted", 0))
    return d


@router.get("/proposals")
async def list_proposals(
    status: str | None = Query(None, description="pending | auto_accepted | approved | rejected"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List compilation proposals for operator review."""
    if not DB_PATH.exists():
        return {"proposals": [], "total": 0}
    conn = _get_db()
    try:
        if not _table_exists(conn, "compilation_proposals"):
            return {"proposals": [], "total": 0}

        where = ""
        params: list[Any] = []
        if status:
            where = "WHERE status = ?"
            params.append(status)
        else:
            # Default view: pending first, then recent reviewed
            where = "WHERE status = 'pending'"

        total_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM compilation_proposals {where}", params,
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = conn.execute(
            f"""
            SELECT * FROM compilation_proposals
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()

        return {
            "proposals": [_proposal_to_dict(r) for r in rows],
            "total": total,
            "threshold": SHADOW_ACCEPT_THRESHOLD,
        }
    finally:
        conn.close()


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str) -> JSONResponse:
    """Get a single compilation proposal by id."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Proposal not found"}, status_code=404)
    conn = _get_db()
    try:
        if not _table_exists(conn, "compilation_proposals"):
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        row = conn.execute(
            "SELECT * FROM compilation_proposals WHERE id = ?", (proposal_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        return _proposal_to_dict(row)
    finally:
        conn.close()


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str) -> JSONResponse:
    """Operator approves a pending proposal → applies to vault."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Proposal not found"}, status_code=404)

    # Load proposal
    conn = _get_db()
    try:
        if not _table_exists(conn, "compilation_proposals"):
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        row = conn.execute(
            "SELECT * FROM compilation_proposals WHERE id = ?", (proposal_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        prop = _proposal_to_dict(row)
    finally:
        conn.close()

    if prop.get("status") not in ("pending",):
        return JSONResponse(
            {
                "error": f"Proposal is already {prop.get('status')}",
                "proposal": prop,
            },
            status_code=409,
        )

    # Rehydrate the extraction and compilation results
    from engine.intelligence.content.result import ExtractionResult
    from engine.intelligence.compile.engine import CompilationResult

    extraction_data = prop.get("extraction") or {}
    compilation_data = prop.get("compilation") or {}

    extraction = ExtractionResult(
        url=extraction_data.get("url", ""),
        platform=extraction_data.get("platform", ""),
        title=extraction_data.get("title", ""),
        author=extraction_data.get("author", ""),
        content=extraction_data.get("content", ""),
        published_at=extraction_data.get("published_at"),
        media=extraction_data.get("media") or [],
        links=extraction_data.get("links") or [],
        metadata=extraction_data.get("metadata") or {},
        backend=extraction_data.get("backend", "proposal_rehydrate"),
    )
    compilation = CompilationResult(
        summary=compilation_data.get("summary", ""),
        concepts=compilation_data.get("concepts") or [],
        topic=compilation_data.get("topic", ""),
        topic_confidence=float(compilation_data.get("topic_confidence") or 0.0),
        topic_is_new=bool(compilation_data.get("topic_is_new", True)),
        entities=compilation_data.get("entities") or [],
        related_captures=compilation_data.get("related_captures") or [],
        stage_suggestion=int(compilation_data.get("stage_suggestion") or 1),
        template_used=compilation_data.get("template_used", "generic"),
        model=compilation_data.get("model", ""),
        provider=compilation_data.get("provider", ""),
        tokens_in=int(compilation_data.get("tokens_in") or 0),
        tokens_out=int(compilation_data.get("tokens_out") or 0),
        duration_ms=int(compilation_data.get("duration_ms") or 0),
    )

    # Apply to vault via the same helper used by auto-accept
    source_id = prop.get("source_id") or proposal_id
    try:
        applied = _apply_compilation_to_vault(
            item_id=source_id,
            extraction=extraction,
            compilation=compilation,
            relevance_score=None,
        )
    except Exception as e:
        logger.exception("Manual approve vault apply failed for %s", proposal_id)
        return JSONResponse(
            {"error": f"Vault apply failed: {e}"},
            status_code=500,
        )

    _mark_proposal(
        proposal_id,
        status="approved",
        auto_accepted=False,
        vault_path=applied["vault_path"],
        reviewed_by="operator",
    )

    return {
        "status": "approved",
        "proposal_id": proposal_id,
        "vault_path": applied["vault_path"],
        "filename": applied["filename"],
        "topic_index_path": applied["topic_index_path"],
        "links_created": applied["links_created"],
    }


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, request: Request) -> JSONResponse:
    """Operator rejects a pending proposal → no vault mutation."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "Proposal not found"}, status_code=404)

    reason = ""
    try:
        body = await request.json()
        reason = (body or {}).get("reason", "")[:500]
    except Exception:
        pass

    conn = _get_db()
    try:
        if not _table_exists(conn, "compilation_proposals"):
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        row = conn.execute(
            "SELECT status FROM compilation_proposals WHERE id = ?", (proposal_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "Proposal not found"}, status_code=404)
        if row["status"] != "pending":
            return JSONResponse(
                {"error": f"Proposal is already {row['status']}"},
                status_code=409,
            )
    finally:
        conn.close()

    _mark_proposal(
        proposal_id,
        status="rejected",
        reviewed_by="operator",
        reject_reason=reason or "(no reason given)",
    )
    return {"status": "rejected", "proposal_id": proposal_id, "reason": reason}


@router.post("/items/{item_id}/dismiss")
async def dismiss_item(item_id: str) -> JSONResponse:
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
async def mark_read(item_id: str) -> JSONResponse:
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
async def create_source(request: Request) -> JSONResponse:
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
) -> JSONResponse:
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
async def delete_source(source_id: str) -> JSONResponse:
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
