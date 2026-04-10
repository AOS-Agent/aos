"""Topic index file builder.

Idempotent load / update / serialize for vault/knowledge/indexes/<slug>.md
files. Callers (the Pass 2 compile engine, the Pass 3 overnight lint) use
`update_index()` to upsert entries, orientation paragraphs, and open
questions without clobbering operator-authored appendices.

File format:

    ---
    type: index
    topic: crdt-algorithms
    title: CRDT Algorithms
    updated: 2026-04-09T12:00:00Z
    doc_count: 7
    ---

    <!-- AOS:MANAGED START -->
    ## Orientation
    ...
    ## Captures
    - [[...]] (YYYY-MM-DD) — summary
    ...
    <!-- AOS:MANAGED END -->

    <operator appendix preserved verbatim>
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .schema import TopicEntry, TopicIndex

MANAGED_START = "<!-- AOS:MANAGED START -->"
MANAGED_END = "<!-- AOS:MANAGED END -->"

# Section heading -> TopicIndex attribute name
_SECTIONS: list[tuple[str, str, str]] = [
    ("Captures", "captures", "capture"),
    ("Research", "research", "research"),
    ("Synthesis", "synthesis", "synthesis"),
    ("Decisions", "decisions", "decision"),
    ("Expertise", "expertise", "expertise"),
]

# entry.type -> attribute name
_TYPE_TO_ATTR = {
    "capture": "captures",
    "research": "research",
    "synthesis": "synthesis",
    "decision": "decisions",
    "expertise": "expertise",
}

_ENTRY_RE = re.compile(
    r"^\-\s+\[\[(?P<stem>[^\]]+)\]\]\s*(?:\((?P<date>\d{4}-\d{2}-\d{2})\))?\s*(?:—\s*(?P<summary>.*))?$"
)


def slugify(text: str) -> str:
    """Convert a topic name to a filesystem-safe slug."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:60] or "untitled"


def _default_vault() -> Path:
    return Path.home() / "vault"


def _index_path(slug: str, vault_dir: Path | None) -> Path:
    vault = vault_dir or _default_vault()
    return vault / "knowledge" / "indexes" / f"{slug}.md"


def load_index(slug: str, vault_dir: Path | None = None) -> TopicIndex:
    """Read and parse a topic index file.

    If the file doesn't exist, returns an empty TopicIndex with the given
    slug and a titlecased fallback title.
    """
    path = _index_path(slug, vault_dir)
    if not path.exists():
        return TopicIndex(slug=slug, title=slug.replace("-", " ").title())

    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    meta: dict = yaml.safe_load(frontmatter) or {} if frontmatter else {}

    idx = TopicIndex(
        slug=meta.get("topic") or slug,
        title=meta.get("title") or slug.replace("-", " ").title(),
        updated=meta.get("updated", "") or "",
    )

    managed, appendix = _split_managed(body)
    idx._operator_appendix = appendix
    _parse_managed(managed, idx)
    return idx


def update_index(
    slug: str,
    *,
    title: str | None = None,
    entry: TopicEntry | None = None,
    orientation: str | None = None,
    open_questions: list[str] | None = None,
    vault_dir: Path | None = None,
) -> Path:
    """Idempotent upsert of a topic index file.

    Loads the existing file (or creates an empty one), applies updates,
    dedupes entries by path, sorts sections by date descending, bumps
    the `updated` timestamp, and writes the file. Returns the path.
    """
    idx = load_index(slug, vault_dir)

    if title is not None:
        idx.title = title
    if orientation is not None:
        idx.orientation = orientation.strip()
    if open_questions is not None:
        idx.open_questions = list(open_questions)

    if entry is not None:
        attr = _TYPE_TO_ATTR.get(entry.type)
        if attr is None:
            raise ValueError(f"unknown entry type: {entry.type!r}")
        entries: list[TopicEntry] = getattr(idx, attr)
        # Dedupe by path (update-in-place)
        for i, existing in enumerate(entries):
            if existing.path == entry.path:
                entries[i] = entry
                break
        else:
            entries.append(entry)
        entries.sort(key=lambda e: e.date, reverse=True)

    idx.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = _index_path(slug, vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(idx), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---\n"):
        return "", raw
    rest = raw[4:]
    end = rest.find("\n---\n")
    if end == -1:
        return "", raw
    return rest[:end], rest[end + 5 :]


def _split_managed(body: str) -> tuple[str, str]:
    """Return (managed_block_content, operator_appendix)."""
    start = body.find(MANAGED_START)
    if start == -1:
        return "", body.strip()
    end = body.find(MANAGED_END, start)
    if end == -1:
        return body[start + len(MANAGED_START) :].strip(), ""
    managed = body[start + len(MANAGED_START) : end].strip()
    appendix = body[end + len(MANAGED_END) :].strip()
    return managed, appendix


def _parse_managed(managed: str, idx: TopicIndex) -> None:
    """Populate idx sections from a managed-block body."""
    if not managed:
        return

    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is None:
            return
        content = [ln for ln in buffer if ln.strip()]
        if current == "orientation":
            idx.orientation = "\n".join(content).strip()
        elif current == "open_questions":
            idx.open_questions = [
                ln.lstrip("- ").strip() for ln in content if ln.lstrip().startswith("-")
            ]
        else:
            # one of the entry sections
            entries = getattr(idx, current)
            type_name = next(
                (t for h, attr, t in _SECTIONS if attr == current), "capture"
            )
            for ln in content:
                m = _ENTRY_RE.match(ln.strip())
                if not m:
                    continue
                stem = m.group("stem")
                date = m.group("date") or ""
                summary = (m.group("summary") or "").strip()
                entries.append(
                    TopicEntry(
                        path=f"knowledge/{current}/{stem}.md",
                        title=stem,
                        type=type_name,
                        stage=_default_stage(type_name),
                        date=date,
                        summary=summary,
                    )
                )

    for line in managed.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            buffer = []
            if heading == "Orientation":
                current = "orientation"
            elif heading == "Open Questions":
                current = "open_questions"
            else:
                match = next(
                    (attr for h, attr, _ in _SECTIONS if h == heading), None
                )
                current = match
            continue
        buffer.append(line)
    flush()


def _default_stage(type_name: str) -> int:
    return {
        "capture": 2,
        "research": 3,
        "synthesis": 4,
        "decision": 5,
        "expertise": 6,
    }.get(type_name, 2)


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def _serialize(idx: TopicIndex) -> str:
    meta = {
        "type": "index",
        "topic": idx.slug,
        "title": idx.title,
        "updated": idx.updated,
        "doc_count": idx.doc_count,
    }
    frontmatter = yaml.safe_dump(meta, sort_keys=False).strip()

    lines: list[str] = ["---", frontmatter, "---", "", MANAGED_START, ""]

    if idx.orientation:
        lines.append("## Orientation")
        lines.append("")
        lines.append(idx.orientation.strip())
        lines.append("")

    for heading, attr, _ in _SECTIONS:
        entries: list[TopicEntry] = getattr(idx, attr)
        if not entries:
            continue
        lines.append(f"## {heading}")
        for e in entries:
            stem = Path(e.path).stem
            date_part = f" ({e.date})" if e.date else ""
            summary_part = f" — {e.summary}" if e.summary else ""
            lines.append(f"- [[{stem}]]{date_part}{summary_part}")
        lines.append("")

    if idx.open_questions:
        lines.append("## Open Questions")
        for q in idx.open_questions:
            lines.append(f"- {q}")
        lines.append("")

    lines.append(MANAGED_END)

    if idx._operator_appendix:
        lines.append("")
        lines.append(idx._operator_appendix.strip())
        lines.append("")

    # Ensure trailing newline, no duplicate blank lines at tail
    text = "\n".join(lines).rstrip() + "\n"
    return text
