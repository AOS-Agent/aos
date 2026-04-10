"""Intelligence Adapter — Captures from the intelligence feed and vault.

A CAPTURE represents a vault-saved piece of external content. Two sources
feed this adapter:

1. The ``intelligence_briefs`` table in qareen.db — rows where
   ``vault_path IS NOT NULL`` and ``status = 'saved'``. These are briefs
   that were promoted to the vault by the operator.
2. ``~/vault/knowledge/captures/*.md`` — walked directly to pick up
   captures created outside the intelligence engine (extract skill,
   ramble, bridge forwards).

Captures from the briefs table take precedence: if a vault file has a
``brief_id`` in its frontmatter matching a briefs row, the filesystem
walker skips it to avoid duplicates.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..model import SearchResult
from ..types import (
    Capture,
    ContextCard,
    Link,
    LinkType,
    ObjectType,
)
from .base import Adapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter_dict, body).

    Returns ({}, full_content) when frontmatter is missing or malformed.
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    raw_yaml = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    try:
        fm = yaml.safe_load(raw_yaml)
        if not isinstance(fm, dict):
            return {}, content
        return fm, body
    except yaml.YAMLError:
        return {}, content


def _parse_date(val: Any) -> datetime | None:
    """Best-effort date parsing from frontmatter or SQL values."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip().strip('"').strip("'")
    if not s:
        return None
    # ISO-like first
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _tags_from(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [t.strip() for t in val.split(",") if t.strip()]
    if isinstance(val, list):
        return [str(t) for t in val]
    return []


# ---------------------------------------------------------------------------
# Intelligence Adapter
# ---------------------------------------------------------------------------

class IntelligenceAdapter(Adapter):
    """Adapter for CAPTURE objects (intelligence_briefs + vault captures)."""

    def __init__(
        self,
        vault_dir: str,
        qareen_db_path: str,
    ) -> None:
        self._vault = Path(vault_dir)
        self._db_path = qareen_db_path
        self._captures_dir = self._vault / "knowledge" / "captures"

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.CAPTURE

    # -- DB helpers -------------------------------------------------------

    def _db(self) -> sqlite3.Connection | None:
        """Open a connection to qareen.db, or None if unavailable."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.warning("IntelligenceAdapter: cannot open qareen.db: %s", e)
            return None

    def _has_briefs_table(self, conn: sqlite3.Connection) -> bool:
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_briefs'"
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    # -- Row/file parsers --------------------------------------------------

    def _capture_from_row(self, row: sqlite3.Row) -> Capture:
        keys = row.keys()
        return Capture(
            id=row["id"],
            title=row["title"] or "(untitled)",
            source_url=row["url"] if "url" in keys else None,
            platform=row["platform"] if "platform" in keys else None,
            vault_path=row["vault_path"] if "vault_path" in keys else None,
            created_at=_parse_date(row["created_at"]) if "created_at" in keys else None,
            author=row["author"] if "author" in keys else None,
            summary=row["summary"] if "summary" in keys else None,
            tags=[],
            project=row["project_id"] if "project_id" in keys else None,
            brief_id=row["id"],
        )

    def _capture_from_file(self, abs_path: Path) -> Capture | None:
        """Parse a vault capture markdown file into a Capture."""
        try:
            raw = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("IntelligenceAdapter: cannot read %s: %s", abs_path, e)
            return None

        fm, _body = _parse_frontmatter(raw)

        try:
            rel_path = str(abs_path.relative_to(self._vault))
        except ValueError:
            rel_path = str(abs_path)

        stem = abs_path.stem
        title = fm.get("title") or stem.replace("-", " ").title()
        source_url = fm.get("source_url") or fm.get("source") or fm.get("url")
        if isinstance(source_url, list):
            source_url = source_url[0] if source_url else None
        platform = fm.get("platform")
        created_at = _parse_date(fm.get("date") or fm.get("created_at"))
        author = fm.get("author")
        summary = fm.get("summary")
        tags = _tags_from(fm.get("tags"))
        project = fm.get("project")
        if isinstance(project, list):
            project = project[0] if project else None
        brief_id = fm.get("brief_id")

        return Capture(
            id=stem,
            title=str(title),
            source_url=str(source_url) if source_url else None,
            platform=str(platform) if platform else None,
            vault_path=rel_path,
            created_at=created_at,
            author=str(author) if author else None,
            summary=str(summary) if summary else None,
            tags=tags,
            project=str(project) if project else None,
            brief_id=str(brief_id) if brief_id else None,
        )

    # -- Collection -------------------------------------------------------

    def _list_from_briefs(
        self,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any] | None,
    ) -> tuple[list[Capture], set[str]]:
        """Return (captures, vault_paths_covered) from intelligence_briefs.

        Only returns rows that have been saved to the vault.
        """
        conn = self._db()
        if conn is None:
            return [], set()
        try:
            if not self._has_briefs_table(conn):
                return [], set()
            query = (
                "SELECT * FROM intelligence_briefs "
                "WHERE vault_path IS NOT NULL AND status = 'saved'"
            )
            params: list[Any] = []
            if filters:
                if "platform" in filters:
                    query += " AND platform = ?"
                    params.append(filters["platform"])
                if "project" in filters:
                    query += " AND project_id = ?"
                    params.append(filters["project"])
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as e:
            logger.warning("IntelligenceAdapter: briefs query failed: %s", e)
            return [], set()
        finally:
            conn.close()

        captures = [self._capture_from_row(r) for r in rows]
        covered = {c.vault_path for c in captures if c.vault_path}
        return captures, covered

    def _walk_vault_captures(
        self,
        *,
        skip_paths: set[str],
    ) -> list[Capture]:
        """Walk ~/vault/knowledge/captures/*.md for captures not in briefs."""
        if not self._captures_dir.is_dir():
            return []
        results: list[Capture] = []
        try:
            for root, _dirs, files in os.walk(self._captures_dir):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    abs_path = Path(root) / fname
                    try:
                        rel_path = str(abs_path.relative_to(self._vault))
                    except ValueError:
                        continue
                    if rel_path in skip_paths:
                        continue
                    cap = self._capture_from_file(abs_path)
                    if cap is not None:
                        results.append(cap)
        except OSError as e:
            logger.warning("IntelligenceAdapter: vault walk failed: %s", e)
            return results
        return results

    def _all_captures(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Capture]:
        # Pull a generous window from briefs, then fill with vault captures.
        briefs, covered = self._list_from_briefs(
            limit=max(limit + offset, 200),
            offset=0,
            filters=filters,
        )
        walked = self._walk_vault_captures(skip_paths=covered)

        # Merge and sort by created_at desc (None goes last).
        # Normalize datetimes to naive UTC so tz-aware and tz-naive rows
        # can be compared (vault YAML dates often have timezone offsets,
        # briefs rows don't).
        def _sort_key(c: Capture):
            dt = c.created_at
            if dt is None:
                return datetime.min
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        merged: list[Capture] = briefs + walked
        merged.sort(key=_sort_key, reverse=True)
        return merged

    # -- Adapter interface -------------------------------------------------

    def get(self, object_id: str) -> Capture | None:
        """Get a capture by brief id or by vault filename stem."""
        # Try briefs first
        conn = self._db()
        if conn is not None:
            try:
                if self._has_briefs_table(conn):
                    row = conn.execute(
                        "SELECT * FROM intelligence_briefs "
                        "WHERE id = ? AND vault_path IS NOT NULL AND status = 'saved'",
                        (object_id,),
                    ).fetchone()
                    if row is not None:
                        return self._capture_from_row(row)
            except sqlite3.Error as e:
                logger.warning("IntelligenceAdapter.get: briefs lookup failed: %s", e)
            finally:
                conn.close()

        # Fall back to vault filename stem
        if self._captures_dir.is_dir():
            candidate = self._captures_dir / f"{object_id}.md"
            if candidate.is_file():
                return self._capture_from_file(candidate)
        return None

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Capture]:
        """List captures, newest first."""
        all_caps = self._all_captures(filters=filters, limit=limit, offset=offset)
        return all_caps[offset: offset + limit]

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        return len(self._all_captures(filters=filters, limit=10_000, offset=0))

    def create(self, obj: Capture) -> Capture:
        """Captures are created by the intelligence engine or extract skill.

        This adapter does not own capture creation — it's a read surface.
        """
        raise NotImplementedError(
            "Captures are created by the intelligence engine or skills, "
            "not via the ontology adapter."
        )

    def update(self, object_id: str, fields: dict[str, Any]) -> Capture | None:
        raise NotImplementedError(
            "Captures are immutable via the ontology. Edit the source row "
            "or vault file directly."
        )

    def delete(self, object_id: str) -> bool:
        """Captures must not be deleted via the API — operator approval required."""
        return False

    # -- Search -----------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Simple case-insensitive title/summary match over captures.

        Full QMD search is already handled by VaultAdapter; this is a
        lightweight lookup scoped to CAPTURE objects.
        """
        if not query:
            return []
        needle = query.lower()
        results: list[SearchResult] = []
        try:
            for cap in self._all_captures(limit=500, offset=0):
                haystack = " ".join(
                    s for s in (cap.title, cap.summary or "", cap.source_url or "")
                    if s
                ).lower()
                if needle in haystack:
                    snippet = cap.summary or cap.source_url or ""
                    results.append(SearchResult(
                        object_type=ObjectType.CAPTURE,
                        object_id=cap.id,
                        title=cap.title,
                        snippet=snippet[:200],
                        score=1.0 if needle in (cap.title or "").lower() else 0.5,
                        obj=cap,
                    ))
                    if len(results) >= limit:
                        break
        except Exception as e:
            logger.warning("IntelligenceAdapter.search failed: %s", e)
            return []
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # -- Links (stored in qareen.db links table) --------------------------

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        conn = self._db()
        if conn is None:
            return []
        try:
            query = (
                "SELECT to_id FROM links "
                "WHERE from_type = 'capture' AND from_id = ? AND to_type = ?"
            )
            params: list[Any] = [obj_id, target_type.value]
            if link_type is not None:
                query += " AND link_type = ?"
                params.append(link_type.value)
            query += " LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [row["to_id"] for row in rows]
        except sqlite3.Error as e:
            logger.warning("IntelligenceAdapter.get_links failed: %s", e)
            return []
        finally:
            conn.close()

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        now = datetime.now().isoformat()
        link_id = str(uuid.uuid4())[:8]
        props = json.dumps(metadata) if metadata else None

        conn = self._db()
        if conn is not None:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO links "
                    "(id, link_type, from_type, from_id, to_type, to_id, "
                    "direction, properties, created_at, created_by) "
                    "VALUES (?, ?, 'capture', ?, ?, ?, 'directed', ?, ?, 'intelligence_adapter')",
                    (
                        link_id,
                        link_type.value,
                        source_id,
                        target_type.value,
                        target_id,
                        props,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.warning("IntelligenceAdapter.create_link failed: %s", e)
            finally:
                conn.close()

        return Link(
            link_type=link_type,
            source_type=ObjectType.CAPTURE,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.now(),
        )

    # -- Context card ------------------------------------------------------

    def get_context_card(self, object_id: str) -> ContextCard | None:
        conn = self._db()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM context_cards WHERE entity_type = 'capture' AND entity_id = ?",
                (object_id,),
            ).fetchone()
            if not row:
                return None
            return ContextCard(
                entity_type=ObjectType.CAPTURE,
                entity_id=object_id,
                summary=row["summary"],
                key_facts=json.loads(row["key_facts"]) if row["key_facts"] else [],
                recent_activity=json.loads(row["recent_activity"]) if row["recent_activity"] else [],
                open_items=json.loads(row["open_items"]) if row["open_items"] else [],
                built_at=datetime.fromisoformat(row["built_at"]) if row["built_at"] else datetime.now(),
                stale_after=datetime.fromisoformat(row["stale_after"]) if row["stale_after"] else None,
            )
        except (sqlite3.Error, json.JSONDecodeError, OSError) as e:
            logger.warning("IntelligenceAdapter.get_context_card failed: %s", e)
            return None
        finally:
            conn.close()
