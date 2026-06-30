"""Initiatives API.

Surfaces the vault initiative documents (~/vault/knowledge/initiatives/*.md) as
structured data + rendered content, so the Work UI can show the strategic layer
(Goal -> Initiative -> Project -> Task) and render an initiative's doc inline.

Read-only: the source of truth stays the markdown files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["initiatives"])

INIT_DIR = Path.home() / "vault" / "knowledge" / "initiatives"


def _json_safe(value: Any) -> Any:
    """Make a frontmatter value JSON-serializable (dates -> iso strings, etc.)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse(path: Path) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body)."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm: dict = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            try:
                import yaml
                fm = yaml.safe_load(raw[3:end]) or {}
            except Exception:
                fm = {}
            body = raw[end + 4:].lstrip("\n")
    return (fm if isinstance(fm, dict) else {}), body


@router.get("/initiatives")
async def list_initiatives() -> JSONResponse:
    """List every initiative with its frontmatter (status, appetite, linked project)."""
    items = []
    if INIT_DIR.exists():
        for p in sorted(INIT_DIR.glob("*.md")):
            try:
                fm, _ = _parse(p)
            except Exception:
                fm = {}
            items.append({
                "slug": p.stem,
                "title": fm.get("title") or p.stem,
                "status": _json_safe(fm.get("status")),
                "stage": _json_safe(fm.get("stage")),
                "appetite": _json_safe(fm.get("appetite")),
                "project": _json_safe(fm.get("project")),
                "tags": _json_safe(fm.get("tags") or []),
                "date": _json_safe(fm.get("date")),
                "updated": _json_safe(fm.get("updated") or fm.get("date")),
            })
    return JSONResponse({"initiatives": items, "total": len(items)})


@router.get("/initiatives/{slug}")
async def get_initiative(slug: str) -> JSONResponse:
    """Return a single initiative's frontmatter + full markdown body."""
    safe = slug.replace("/", "").replace("\\", "").replace("..", "")
    p = INIT_DIR / f"{safe}.md"
    if not p.exists():
        return JSONResponse({"error": f"Initiative not found: {safe}"}, status_code=404)
    try:
        fm, body = _parse(p)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({
        "slug": safe,
        "title": fm.get("title") or safe,
        "status": _json_safe(fm.get("status")),
        "stage": _json_safe(fm.get("stage")),
        "appetite": _json_safe(fm.get("appetite")),
        "project": _json_safe(fm.get("project")),
        "tags": _json_safe(fm.get("tags") or []),
        "frontmatter": _json_safe(fm),
        "content": body,
    })
