"""Vault Adapter — Notes and Decisions from the markdown vault.

Notes are VIRTUAL OBJECTS. They live as markdown files in ~/vault/,
never duplicated into SQLite. This adapter reads from the filesystem
and QMD search, and stores only cross-store links in qareen.db.

The Note.id is the path relative to ~/vault/ (e.g.,
"knowledge/decisions/pricing.md").
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..types import (
    ContextCard, Decision, Link, LinkType, Note, NoteStage, ObjectType,
)
from ..model import SearchResult
from .base import Adapter


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map note_type values to subdirectories within knowledge/
_TYPE_TO_SUBDIR: dict[str, str] = {
    "capture": "knowledge/captures",
    "research": "knowledge/research",
    "reference": "knowledge/references",
    "synthesis": "knowledge/synthesis",
    "decision": "knowledge/decisions",
    "expertise": "knowledge/expertise",
    "initiative": "knowledge/initiatives",
    "spec": "knowledge/specs",
    # Log-side types
    "daily": "log",
    "session": "log/sessions",
    "review": "log",
    "friction": "log/friction",
}

# Reverse: subdirectory name -> note_type
_SUBDIR_TO_TYPE: dict[str, str] = {
    "captures": "capture",
    "research": "research",
    "references": "reference",
    "synthesis": "synthesis",
    "decisions": "decision",
    "expertise": "expertise",
    "initiatives": "initiative",
    "specs": "spec",
    "sessions": "session",
    "friction": "friction",
}

# Map note_type -> NoteStage (where a default makes sense)
_TYPE_TO_STAGE: dict[str, NoteStage] = {
    "capture": NoteStage.CAPTURE,
    "research": NoteStage.RESEARCH,
    "synthesis": NoteStage.SYNTHESIS,
    "decision": NoteStage.DECISION,
    "expertise": NoteStage.EXPERTISE,
}


def _slugify(text: str) -> str:
    """Convert text to a kebab-case filename slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter_dict, body).

    Returns ({}, full_content) if no valid frontmatter is found.
    """
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
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


def _build_frontmatter(fields: dict[str, Any]) -> str:
    """Serialize a dict to YAML frontmatter block."""
    dumped = yaml.dump(fields, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{dumped}---\n"


def _parse_date(val: Any) -> datetime | None:
    """Best-effort date parsing from frontmatter values."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip().strip('"').strip("'")
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_stage(val: Any) -> NoteStage:
    """Parse a stage value from frontmatter into NoteStage enum."""
    if val is None:
        return NoteStage.CAPTURE
    if isinstance(val, int) and 1 <= val <= 6:
        return NoteStage(val)
    try:
        return NoteStage(int(val))
    except (ValueError, TypeError):
        return NoteStage.CAPTURE


def _infer_type_from_path(rel_path: str) -> str:
    """Infer note_type from the relative vault path."""
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "knowledge":
        return _SUBDIR_TO_TYPE.get(parts[1], "capture")
    if len(parts) >= 1 and parts[0] == "log":
        if len(parts) >= 2:
            return _SUBDIR_TO_TYPE.get(parts[1], "daily")
        return "daily"
    return "capture"


# ---------------------------------------------------------------------------
# Vault Adapter
# ---------------------------------------------------------------------------

class VaultAdapter(Adapter):
    """Adapter for Notes and Decisions stored as markdown in the vault.

    Notes are virtual — this adapter reads from the filesystem, never
    duplicates content into SQLite. Links FROM notes to other entities
    are stored in qareen.db's links table.
    """

    def __init__(
        self,
        vault_dir: str,
        qareen_db_path: str,
        qmd_path: str = os.path.expanduser("~/.bun/bin/qmd"),
    ) -> None:
        self._vault = Path(vault_dir)
        self._db_path = qareen_db_path
        self._qmd_path = qmd_path
        # Cache: relative_path -> (mtime, parsed Note/Decision)
        self._cache: dict[str, tuple[float, Note | Decision]] = {}

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.NOTE

    # -- Helpers --------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        """Open a connection to qareen.db."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _abs(self, rel_path: str) -> Path:
        """Resolve a relative vault path to absolute."""
        return self._vault / rel_path

    def _rel(self, abs_path: Path) -> str:
        """Get the vault-relative path string."""
        return str(abs_path.relative_to(self._vault))

    def _parse_file(self, rel_path: str) -> Note | Decision | None:
        """Parse a vault markdown file into a Note or Decision.

        Uses an mtime-based cache to avoid re-parsing unchanged files.
        Returns None if the file doesn't exist or can't be parsed.
        """
        abs_path = self._abs(rel_path)
        if not abs_path.is_file():
            return None

        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            return None

        # Check cache
        cached = self._cache.get(rel_path)
        if cached and cached[0] == mtime:
            return cached[1]

        try:
            raw = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        fm, body = _parse_frontmatter(raw)

        # Determine note type
        note_type = fm.get("type", None)
        if note_type is None:
            note_type = _infer_type_from_path(rel_path)
        # Normalize some variants
        type_aliases = {
            "content-extract": "capture",
            "material": "capture",
            "compiled-pattern": "expertise",
            "pattern": "expertise",
        }
        note_type = type_aliases.get(note_type, note_type)

        title = fm.get("title", abs_path.stem.replace("-", " ").title())

        # Parse tags — handle both list and string
        raw_tags = fm.get("tags", [])
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        else:
            tags = []

        date = _parse_date(fm.get("date"))

        stage_val = fm.get("stage")
        if stage_val is not None:
            stage = _parse_stage(stage_val)
        else:
            stage = _TYPE_TO_STAGE.get(note_type, NoteStage.CAPTURE)

        project = fm.get("project")
        if isinstance(project, list):
            project = project[0] if project else None
        source_ref = fm.get("source_ref", fm.get("source"))
        if isinstance(source_ref, list):
            source_ref = ", ".join(str(s) for s in source_ref)
        elif source_ref is not None:
            source_ref = str(source_ref)

        # Build the appropriate dataclass
        if note_type == "decision":
            obj = Decision(
                id=rel_path,
                title=str(title),
                rationale=body,
                date=date,
                stakeholders=[],
                project=str(project) if project else None,
                tags=tags,
                supersedes=fm.get("supersedes"),
                status=fm.get("status", "active"),
            )
        else:
            obj = Note(
                id=rel_path,
                title=str(title),
                note_type=note_type,
                stage=stage,
                date=date,
                tags=tags,
                project=str(project) if project else None,
                source_ref=source_ref,
                content=body,
            )

        self._cache[rel_path] = (mtime, obj)
        return obj

    def _walk_dir(self, subdir: str) -> list[str]:
        """Walk a subdirectory and return relative vault paths for all .md files."""
        target = self._vault / subdir
        if not target.is_dir():
            return []
        results: list[str] = []
        for root, _dirs, files in os.walk(target):
            for fname in files:
                if fname.endswith(".md"):
                    abs_path = Path(root) / fname
                    results.append(self._rel(abs_path))
        return results

    def _scan_dirs_for_type(self, note_type: str | None) -> list[str]:
        """Determine which subdirectories to scan based on note_type filter."""
        if note_type is not None:
            subdir = _TYPE_TO_SUBDIR.get(note_type)
            if subdir:
                return [subdir]
            # Unknown type — scan everything
            return ["knowledge", "log"]
        return ["knowledge", "log"]

    def _collect_and_filter(
        self,
        filters: dict[str, Any] | None = None,
    ) -> list[Note | Decision]:
        """Collect notes matching filters, sorted by date descending."""
        filters = filters or {}
        note_type = filters.get("note_type")
        tag_filter = filters.get("tags")
        project_filter = filters.get("project")
        stage_filter = filters.get("stage")

        scan_dirs = self._scan_dirs_for_type(note_type)
        all_paths: list[str] = []
        for d in scan_dirs:
            all_paths.extend(self._walk_dir(d))

        results: list[Note | Decision] = []
        for rel_path in all_paths:
            obj = self._parse_file(rel_path)
            if obj is None:
                continue

            # Apply filters
            if note_type is not None:
                obj_type = obj.note_type if isinstance(obj, Note) else "decision"
                if obj_type != note_type:
                    continue

            if tag_filter is not None:
                obj_tags = obj.tags
                if isinstance(tag_filter, str):
                    if tag_filter not in obj_tags:
                        continue
                elif isinstance(tag_filter, list):
                    if not any(t in obj_tags for t in tag_filter):
                        continue

            if project_filter is not None:
                obj_project = obj.project
                if obj_project != project_filter:
                    continue

            if stage_filter is not None:
                if isinstance(obj, Note):
                    if isinstance(stage_filter, NoteStage):
                        if obj.stage != stage_filter:
                            continue
                    elif isinstance(stage_filter, int):
                        if obj.stage.value != stage_filter:
                            continue

            results.append(obj)

        # Sort by date descending (None dates go to the end)
        results.sort(
            key=lambda n: n.date or datetime.min,
            reverse=True,
        )
        return results

    # -- Adapter interface ----------------------------------------------------

    def get(self, object_id: str) -> Note | Decision | None:
        """Get a note by its vault-relative path."""
        return self._parse_file(object_id)

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Note | Decision]:
        """List notes matching filters, sorted by date descending."""
        results = self._collect_and_filter(filters)
        return results[offset: offset + limit]

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        """Count notes matching filters."""
        return len(self._collect_and_filter(filters))

    def create(self, obj: Note | Decision) -> Note | Decision:
        """Write a new markdown file to the vault.

        Returns the object with its id set to the new file's relative path.
        """
        if isinstance(obj, Decision):
            note_type = "decision"
        else:
            note_type = getattr(obj, "note_type", "capture")

        subdir = _TYPE_TO_SUBDIR.get(note_type, "knowledge/captures")
        target_dir = self._vault / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        slug = _slugify(obj.title)
        if not slug:
            slug = "untitled"
        filename = f"{slug}.md"
        target = target_dir / filename

        # Avoid collisions
        counter = 1
        while target.exists():
            filename = f"{slug}-{counter}.md"
            target = target_dir / filename
            counter += 1

        # Build frontmatter
        fm: dict[str, Any] = {"title": obj.title, "type": note_type}
        date_val = obj.date or datetime.now()
        fm["date"] = date_val.strftime("%Y-%m-%d")
        fm["tags"] = obj.tags if obj.tags else []

        if isinstance(obj, Decision):
            fm["status"] = obj.status
            if obj.supersedes:
                fm["supersedes"] = obj.supersedes
            body = obj.rationale
        else:
            if obj.stage:
                fm["stage"] = obj.stage.value
            if obj.source_ref:
                fm["source_ref"] = obj.source_ref
            body = obj.content

        if obj.project:
            fm["project"] = obj.project

        content = _build_frontmatter(fm) + "\n" + body
        target.write_text(content, encoding="utf-8")

        rel_path = self._rel(target)
        # Update the object id and return
        if isinstance(obj, Decision):
            return Decision(
                id=rel_path,
                title=obj.title,
                rationale=obj.rationale,
                date=date_val,
                stakeholders=obj.stakeholders,
                project=obj.project,
                tags=obj.tags,
                supersedes=obj.supersedes,
                status=obj.status,
            )
        else:
            return Note(
                id=rel_path,
                title=obj.title,
                note_type=note_type,
                stage=obj.stage,
                date=date_val,
                tags=obj.tags,
                project=obj.project,
                source_ref=obj.source_ref,
                content=obj.content,
            )

    def update(self, object_id: str, fields: dict[str, Any]) -> Note | Decision | None:
        """Update frontmatter fields on an existing note.

        Preserves the markdown body. Returns the updated object.
        """
        abs_path = self._abs(object_id)
        if not abs_path.is_file():
            return None

        try:
            raw = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        fm, body = _parse_frontmatter(raw)

        # Merge updates into frontmatter
        for key, val in fields.items():
            if key in ("content", "rationale"):
                body = val
            elif key == "stage" and isinstance(val, NoteStage):
                fm["stage"] = val.value
            elif key == "date" and isinstance(val, datetime):
                fm["date"] = val.strftime("%Y-%m-%d")
            else:
                fm[key] = val

        content = _build_frontmatter(fm) + "\n" + body
        abs_path.write_text(content, encoding="utf-8")

        # Invalidate cache and re-parse
        self._cache.pop(object_id, None)
        return self._parse_file(object_id)

    def delete(self, object_id: str) -> bool:
        """Vault notes should not be deleted via the API.

        Returns False always. Use the vault directly for deletion.
        """
        return False

    # -- Search via QMD -------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Search the vault via QMD subprocess.

        Returns SearchResult objects. Handles QMD being unavailable
        gracefully by returning an empty list.
        """
        try:
            result = subprocess.run(
                [self._qmd_path, "query", query, "--json", "-n", str(limit)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return []

            data = json.loads(result.stdout)
            if not isinstance(data, list):
                return []

        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError):
            return []

        results: list[SearchResult] = []
        for item in data:
            # QMD returns file paths like "qmd://knowledge/research/foo.md"
            file_path = item.get("file", "")
            # Strip the qmd:// prefix to get the vault-relative path
            if file_path.startswith("qmd://"):
                rel_path = file_path[6:]
            else:
                rel_path = file_path

            title = item.get("title", "")
            score = float(item.get("score", 0.0))
            snippet = item.get("snippet", "")
            # Clean up snippet — remove the @@ line markers
            snippet = re.sub(r"@@ -\d+,\d+ @@.*?\n", "", snippet).strip()

            results.append(SearchResult(
                object_type=ObjectType.NOTE,
                object_id=rel_path,
                title=title,
                snippet=snippet,
                score=score,
            ))

        return results

    # -- Links (stored in qareen.db) ------------------------------------------

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get ids of objects linked from this note.

        Queries the links table in qareen.db.
        """
        try:
            conn = self._db()
            query = (
                "SELECT to_id FROM links "
                "WHERE from_type = 'note' AND from_id = ? AND to_type = ?"
            )
            params: list[Any] = [obj_id, target_type.value]

            if link_type is not None:
                query += " AND link_type = ?"
                params.append(link_type.value)

            query += " LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            conn.close()
            return [row["to_id"] for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Create a link from a note to another entity in qareen.db."""
        import uuid

        now = datetime.now().isoformat()
        link_id = str(uuid.uuid4())[:8]
        props = json.dumps(metadata) if metadata else None

        conn = self._db()
        conn.execute(
            "INSERT OR IGNORE INTO links "
            "(id, link_type, from_type, from_id, to_type, to_id, direction, properties, created_at, created_by) "
            "VALUES (?, ?, 'note', ?, ?, ?, 'directed', ?, ?, 'vault_adapter')",
            (link_id, link_type.value, source_id, target_type.value, target_id, props, now),
        )
        conn.commit()
        conn.close()

        return Link(
            link_type=link_type,
            source_type=ObjectType.NOTE,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.now(),
        )

    # -- Context cards (from qareen.db) ----------------------------------------

    def get_context_card(self, object_id: str) -> ContextCard | None:
        """Get a pre-built context card for a note from qareen.db."""
        try:
            conn = self._db()
            row = conn.execute(
                "SELECT * FROM context_cards WHERE entity_type = 'note' AND entity_id = ?",
                (object_id,),
            ).fetchone()
            conn.close()

            if not row:
                return None

            return ContextCard(
                entity_type=ObjectType.NOTE,
                entity_id=object_id,
                summary=row["summary"],
                key_facts=json.loads(row["key_facts"]) if row["key_facts"] else [],
                recent_activity=json.loads(row["recent_activity"]) if row["recent_activity"] else [],
                open_items=json.loads(row["open_items"]) if row["open_items"] else [],
                built_at=datetime.fromisoformat(row["built_at"]) if row["built_at"] else datetime.now(),
                stale_after=datetime.fromisoformat(row["stale_after"]) if row["stale_after"] else None,
            )
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return None
