"""Vault inventory scanner.

Non-destructive walk of ~/vault/knowledge/. Reads every markdown file,
parses frontmatter, applies the contract, detects orphans, and upserts
rows into the vault_inventory table.

Usage:
    from core.engine.intelligence.inventory import scan_vault
    stats = scan_vault()
    print(f"scanned {stats.total}, issues={stats.with_issues}")

This function is CPU-bound but lightweight (~230 files for a mature
vault; ~5s on this machine). Caller should run it on demand or via
cron (Part 9 adds a 'vault-inventory' cron).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .contract import infer_stage, infer_type, validate

logger = logging.getLogger(__name__)

DEFAULT_VAULT_DIR = Path.home() / "vault"
DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

# Which subfolders under knowledge/ to walk. Others (e.g. knowledge/drafts/)
# are ignored — add them here if new folders get introduced.
KNOWLEDGE_FOLDERS = (
    "captures",
    "research",
    "references",
    "synthesis",
    "decisions",
    "expertise",
    "initiatives",
    "indexes",
)

# Regex for detecting [[wikilinks]] and [text](relative.md) references
_WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_MDLINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+\.md)[^)]*\)")


@dataclass
class InventoryStats:
    total: int = 0
    with_issues: int = 0
    orphans: int = 0
    missing_frontmatter: int = 0
    by_type: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_type is None:
            self.by_type = {}


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter, body)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, content
    if not isinstance(fm, dict):
        return {}, content
    return fm, body


def _find_references(body: str) -> set[str]:
    """Extract every doc reference from a body — both [[wikilinks]] and [text](file.md)."""
    refs: set[str] = set()
    for m in _WIKILINK_RE.findall(body):
        # Wikilinks can be "foo" or "foo|Display" — take the target only
        target = m.split("|", 1)[0].strip()
        if target:
            refs.add(target)
    for m in _MDLINK_RE.findall(body):
        refs.add(m.strip())
    return refs


def _normalize_ref(ref: str) -> str:
    """Normalize a reference into a comparable slug (filename stem, no ext)."""
    r = ref.strip().lower()
    if r.endswith(".md"):
        r = r[:-3]
    # Strip path components — we match on filename stem
    if "/" in r:
        r = r.rsplit("/", 1)[-1]
    return r


def scan_vault(
    vault_dir: Path | None = None,
    db_path: Path | None = None,
) -> InventoryStats:
    """Walk the vault and upsert vault_inventory rows.

    Two passes:
        1. Walk every markdown file, parse frontmatter, collect body text.
           Stage results in-memory keyed by vault-relative path.
        2. Build a global set of slugs referenced by any doc. Second walk
           compares each doc's slug to the reference set to detect orphans.
        3. Write all rows to vault_inventory in a single transaction.

    Returns InventoryStats with aggregate counts.
    """
    vault = vault_dir or DEFAULT_VAULT_DIR
    db = db_path or DEFAULT_DB_PATH
    knowledge_dir = vault / "knowledge"

    if not knowledge_dir.is_dir():
        logger.warning("Vault knowledge dir not found: %s", knowledge_dir)
        return InventoryStats()

    # Pass 1: walk files, collect per-doc state + references in one sweep
    docs: dict[str, dict[str, Any]] = {}

    for folder in KNOWLEDGE_FOLDERS:
        folder_path = knowledge_dir / folder
        if not folder_path.is_dir():
            continue
        for md_path in folder_path.rglob("*.md"):
            try:
                stat = md_path.stat()
                content = md_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to read %s: %s", md_path, e)
                continue

            rel_path = md_path.relative_to(vault)
            frontmatter, body = _parse_frontmatter(content)
            issues, checks = validate(rel_path=rel_path, frontmatter=frontmatter)
            refs = {_normalize_ref(r) for r in _find_references(body)}

            stage_declared = frontmatter.get("stage")
            try:
                stage_declared_int: int | None = (
                    int(stage_declared) if stage_declared is not None else None
                )
            except (TypeError, ValueError):
                stage_declared_int = None

            docs[rel_path.as_posix()] = {
                "rel_path": rel_path,
                "stage": stage_declared_int or infer_stage(rel_path),
                "stage_declared": stage_declared_int,
                "type": (frontmatter.get("type") or infer_type(rel_path)),
                "title": frontmatter.get("title") or md_path.stem,
                "topic": frontmatter.get("topic") or None,
                "has_frontmatter": int(checks["has_frontmatter"]),
                "has_summary": int(checks["has_summary"]),
                "has_concepts": int(checks["has_concepts"]),
                "has_topic": int(checks["has_topic"]),
                "has_source_url": int(checks["has_source_url"]),
                "issues": issues,
                "last_modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "file_size": stat.st_size,
                "word_count": len(body.split()),
                "slug": md_path.stem.lower(),
                "refs": refs,  # normalized slugs this doc references
            }

    # Pass 2: backlink counts + orphan detection (all in-memory, no re-reads)
    for path, info in docs.items():
        slug = info["slug"]
        backlink_count = sum(
            1
            for other_path, other_info in docs.items()
            if other_path != path and slug in other_info["refs"]
        )
        info["backlink_count"] = backlink_count
        # Orphan rule: no incoming links AND stage >= 3 AND not an index
        info["is_orphan"] = int(
            backlink_count == 0
            and (info["stage"] or 0) >= 3
            and info["type"] != "index"
        )

    # Pass 3: write to DB
    now_iso = datetime.now(timezone.utc).isoformat()
    stats = InventoryStats()
    conn = sqlite3.connect(str(db))
    try:
        # Clear stale rows that point at files that no longer exist
        existing_paths = {r[0] for r in conn.execute("SELECT path FROM vault_inventory").fetchall()}
        current_paths = set(docs.keys())
        stale = existing_paths - current_paths
        if stale:
            conn.executemany(
                "DELETE FROM vault_inventory WHERE path = ?",
                [(p,) for p in stale],
            )

        # Upsert each doc
        for path, info in docs.items():
            conn.execute(
                """
                INSERT INTO vault_inventory
                    (path, stage, stage_declared, type, title, topic,
                     has_frontmatter, has_summary, has_concepts, has_topic,
                     has_source_url, backlink_count, is_orphan,
                     compilation_status, issues, last_modified, last_scanned,
                     file_size, word_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    stage              = excluded.stage,
                    stage_declared     = excluded.stage_declared,
                    type               = excluded.type,
                    title              = excluded.title,
                    topic              = excluded.topic,
                    has_frontmatter    = excluded.has_frontmatter,
                    has_summary        = excluded.has_summary,
                    has_concepts       = excluded.has_concepts,
                    has_topic          = excluded.has_topic,
                    has_source_url     = excluded.has_source_url,
                    backlink_count     = excluded.backlink_count,
                    is_orphan          = excluded.is_orphan,
                    issues             = excluded.issues,
                    last_modified      = excluded.last_modified,
                    last_scanned       = excluded.last_scanned,
                    file_size          = excluded.file_size,
                    word_count         = excluded.word_count
                """,
                (
                    path,
                    info["stage"],
                    info["stage_declared"],
                    info["type"],
                    info["title"],
                    info["topic"],
                    info["has_frontmatter"],
                    info["has_summary"],
                    info["has_concepts"],
                    info["has_topic"],
                    info["has_source_url"],
                    info["backlink_count"],
                    info["is_orphan"],
                    "pending",  # compilation_status — updated by compile pass
                    json.dumps(info["issues"]) if info["issues"] else None,
                    info["last_modified"],
                    now_iso,
                    info["file_size"],
                    info["word_count"],
                ),
            )

            stats.total += 1
            if info["issues"]:
                stats.with_issues += 1
            if info["is_orphan"]:
                stats.orphans += 1
            if "missing_frontmatter" in info["issues"]:
                stats.missing_frontmatter += 1
            type_key = info["type"] or "unknown"
            stats.by_type[type_key] = stats.by_type.get(type_key, 0) + 1

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "scan_vault: total=%d issues=%d orphans=%d",
        stats.total, stats.with_issues, stats.orphans,
    )
    return stats


