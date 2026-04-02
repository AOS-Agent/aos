"""Qareen API — Vault / Knowledge routes.

List collections, search via QMD, and read file content.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse

from .schemas import (
    VaultCollectionListResponse,
    VaultCollectionResponse,
    VaultFileResponse,
    VaultSearchResponse,
    VaultSearchResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["vault"])

VAULT_DIR = Path.home() / "vault"
QMD_PATH = os.path.expanduser("~/.bun/bin/qmd")


def _count_md_files(path: Path) -> int:
    """Count .md files recursively in a directory."""
    if not path.is_dir():
        return 0
    count = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            if f.endswith(".md"):
                count += 1
    return count


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter_dict, body)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    raw_yaml = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    try:
        import yaml
        fm = yaml.safe_load(raw_yaml)
        if not isinstance(fm, dict):
            return {}, content
        return fm, body
    except Exception:
        return {}, content


@router.get("/collections", response_model=VaultCollectionListResponse)
async def list_collections(request: Request) -> VaultCollectionListResponse:
    """List all vault collections with document counts."""
    collections = []
    total_docs = 0

    # Standard vault collections
    vault_collections = [
        ("knowledge", VAULT_DIR / "knowledge"),
        ("log", VAULT_DIR / "log"),
    ]

    for name, path in vault_collections:
        count = _count_md_files(path)
        total_docs += count
        collections.append(VaultCollectionResponse(
            name=name,
            path=str(path),
            doc_count=count,
        ))

    return VaultCollectionListResponse(
        collections=collections,
        total_docs=total_docs,
    )


@router.get("/search", response_model=VaultSearchResponse)
async def search_vault(
    request: Request,
    q: str = Query(..., description="Search query", min_length=1),
    collection: str | None = Query(None, description="Limit to a specific collection"),
    limit: int = Query(10, description="Max results", ge=1, le=50),
    min_score: float = Query(0.0, description="Minimum relevance score", ge=0.0, le=1.0),
) -> VaultSearchResponse:
    """Search the vault via QMD hybrid search."""
    results: list[VaultSearchResult] = []

    try:
        cmd = [QMD_PATH, "query", q, "--json", "-n", str(limit)]
        if collection:
            cmd.extend(["-c", collection])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.returncode == 0 and proc.stdout.strip():
            import json
            import re
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                for item in data:
                    score = float(item.get("score", 0.0))
                    if score < min_score:
                        continue
                    file_path = item.get("file", "")
                    if file_path.startswith("qmd://"):
                        file_path = file_path[6:]
                    snippet = item.get("snippet", "")
                    snippet = re.sub(r"@@ -\d+,\d+ @@.*?\n", "", snippet).strip()
                    results.append(VaultSearchResult(
                        path=file_path,
                        title=item.get("title", ""),
                        snippet=snippet,
                        score=score,
                        collection=item.get("collection", ""),
                    ))
    except FileNotFoundError:
        logger.warning("QMD not found at %s", QMD_PATH)
    except subprocess.TimeoutExpired:
        logger.warning("QMD search timed out")
    except Exception:
        logger.exception("QMD search failed")

    return VaultSearchResponse(
        results=results,
        total=len(results),
        query=q,
    )


@router.get("/tree")
async def vault_tree(
    request: Request,
    root: str = Query("", description="Subdirectory within vault to list"),
) -> list[dict[str, Any]]:
    """Return a recursive tree of vault files/folders for the sidebar."""
    base = VAULT_DIR / root if root else VAULT_DIR
    if not base.is_dir():
        return []

    def _build_tree(directory: Path, rel: str = "") -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return nodes
        for entry in entries:
            if entry.name.startswith(".") or entry.name.startswith("__"):
                continue
            rel_path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                children = _build_tree(entry, rel_path)
                count = sum(1 for c in children if c["type"] == "file") + sum(
                    c.get("count", 0) for c in children if c["type"] == "folder"
                )
                nodes.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "folder",
                    "children": children,
                    "count": count,
                })
            elif entry.suffix == ".md":
                nodes.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "file",
                })
        return nodes

    return _build_tree(base, root)


@router.get("/logs")
async def vault_logs(
    request: Request,
) -> list[dict[str, Any]]:
    """Return a list of daily log files with date and title metadata."""
    log_dir = VAULT_DIR / "log"
    if not log_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for entry in sorted(log_dir.iterdir(), reverse=True):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        # Extract date from filename (YYYY-MM-DD.md)
        date_str = entry.stem
        title = date_str
        try:
            content = entry.read_text(encoding="utf-8", errors="replace")
            fm, _ = _parse_frontmatter(content)
            if fm.get("title"):
                title = str(fm["title"])
        except OSError:
            pass
        results.append({
            "date": date_str,
            "title": title,
            "path": f"log/{entry.name}",
        })
    return results


@router.get("/file/{path:path}", response_model=VaultFileResponse)
async def get_file(
    request: Request,
    path: str = PathParam(..., description="Relative file path within the vault"),
) -> VaultFileResponse | JSONResponse:
    """Read a single vault file's content and frontmatter."""
    abs_path = VAULT_DIR / path

    if not abs_path.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)

    # Security check: ensure the resolved path is within the vault
    try:
        abs_path.resolve().relative_to(VAULT_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Access denied"}, status_code=403)

    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return JSONResponse({"error": f"Could not read file: {e}"}, status_code=500)

    fm, body = _parse_frontmatter(content)
    size = abs_path.stat().st_size

    return VaultFileResponse(
        path=path,
        title=fm.get("title"),
        content=body,
        frontmatter=fm,
        size_bytes=size,
    )
