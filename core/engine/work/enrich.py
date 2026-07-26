#!/usr/bin/env python3
"""
Task enricher — link-and-pull, never generate.

The problem: `hre#1.3 "Part 2: The claim spine"` is unreadable on its own. No
description, no acceptance criteria, no link. Yet the full plain-English body
*already exists* in `hre-mvp-scope.md` under `## 4C · Part 2 — The claim spine`.

So this module does not write prose. It finds the section a task came from,
pulls the text verbatim, and records where it came from. Three rules govern it:

1. **Never invent text.** No source section → empty body plus a
   `Conflict(kind="no_body")`. A visible gap beats generated filler.
2. **Always cite.** Every pulled body carries a `body_source` anchor like
   `vault/knowledge/initiatives/hre-mvp-scope.md#4C`, so it is re-syncable and
   the doc stays the source of truth.
3. **Report, never resolve.** When the docs and the tracker disagree about
   whether something is done — or when one doc contradicts itself — that is
   surfaced as a `status_disagreement` conflict. Reconciling it is the
   operator's call, always.

See BRIEF-CONTRACT.md § "Task bodies" and § "New conflict kind".

Storage: the body goes into the column that already exists for it —
`tasks.description` (empty on 1,655 of 1,927 live tasks, which is the
complaint). Its provenance rides along in the `tasks.fields` JSON blob
(`body_source`, `body_synced_at`, `body_hash`, `acceptance`), so no schema
change and no migration. Read it back with `task_body(task)`.

A hand-written description is never overwritten. If a task already carries
prose the enricher didn't write, the pull is reported as skipped and the
operator's words stand. The `<!-- meta: ... -->` comment some tasks smuggle
through `description` is preserved on write, and its `source_ref` is used to
prioritise which doc to look in.

Usage:
    from enrich import enrich_project
    report = enrich_project("hre", dry_run=True)
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from brief_types import Conflict  # noqa: E402

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - yaml ships with AOS
    _yaml = None


# ── Report ──────────────────────────────────────────────────────────────

@dataclass
class EnrichReport:
    """The outcome of one enrich pass.

    The first five fields are the locked contract (BRIEF-CONTRACT.md). The
    three after them are additive conveniences with defaults: `conflicts`
    carries *every* conflict found (both `no_body` and `status_disagreement`,
    so a caller never has to re-derive the no-body ones from `unmatched`),
    `pulled` is what a `--dry-run` renders — the only way to show what *would*
    have been written without writing it — and `skipped` names the tasks whose
    hand-written description was left alone.
    """

    project_id: str
    matched: list[tuple[str, str]] = field(default_factory=list)   # (task_id, anchor)
    unmatched: list[str] = field(default_factory=list)             # task_ids, no section
    disagreements: list[Conflict] = field(default_factory=list)    # status_disagreement
    changed: int = 0

    conflicts: list[Conflict] = field(default_factory=list)        # every conflict
    pulled: dict[str, dict] = field(default_factory=dict)          # task_id -> body payload
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (task_id, why)
    pending: list[str] = field(default_factory=list)               # would change (dry run)


# ── Doc parsing primitives ──────────────────────────────────────────────

# The three status markers the docs use. Nothing else is treated as a status.
MARKERS = {
    "⬜": "not_started",
    "🔶": "in_progress",
    "✅": "done",
}
_MARKER_RE = re.compile("|".join(re.escape(m) for m in MARKERS))

MARKER_ENGLISH = {
    "not_started": "not started",
    "in_progress": "in progress",
    "done": "done",
}

# Task status -> the same three-state vocabulary the docs speak.
TASK_STATE = {
    "done": "done",
    "active": "in_progress",
    "waiting": "in_progress",
    "todo": "not_started",
    "inbox": "not_started",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_PART_RE = re.compile(r"\bpart\s+(\d+)\b", re.IGNORECASE)
# "🔶  0. Content velocity — how fast can we produce lessons?"
_INDEX_RE = re.compile(
    r"^\s*(?P<marker>" + "|".join(re.escape(m) for m in MARKERS) + r")\s*"
    r"(?P<num>\d{1,2})\s*[.)]\s+(?P<label>\S.*?)\s*$"
)
# "4C · Part 2 — The claim spine" -> "4C"
_ANCHOR_RE = re.compile(r"^([0-9]+[A-Za-z]?)\s*[·.)\-–—]")
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.+?)\s*$")
_ACCEPTANCE_RE = re.compile(
    r"\b(acceptance|criteria|done when|definition of done|still to do|"
    r"checklist|success looks like|what done means)\b",
    re.IGNORECASE,
)

# A pulled body must describe the work. A short fragment, a lead-in ending in
# a colon, or a "see this other doc" pointer describes nothing — those are
# skipped in favour of the next real block.
MIN_BODY_CHARS = 80
MIN_BODY_WORDS = 8
MAX_ACCEPTANCE_ITEMS = 12
MAX_STRUCTURED_ROWS = 6
_POINTER_RE = re.compile(
    r"^\s*(full detail|details?|see|source|sources|more|further reading|"
    r"reference|refs?|read)\b",
    re.IGNORECASE,
)
_META_RE = re.compile(r"<!--\s*meta:.*?-->", re.DOTALL)

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "its", "it", "is", "are", "be", "by", "as", "at", "from", "that", "this",
    "how", "what", "we", "our",
}
# A fallback title match must be nearly total — a loose match would attach the
# wrong prose to a task, which is worse than leaving the body empty.
FALLBACK_MIN_RATIO = 0.6
FALLBACK_MIN_SHARED = 2


@dataclass
class Section:
    """One heading and everything under it, up to the next same-or-higher one."""

    level: int
    heading: str            # heading text, markers stripped
    raw_heading: str
    anchor: str             # "4C" or a slug
    line: int               # 1-based line number of the heading
    part_no: int | None
    marker: str | None      # the raw emoji, if the heading carries one
    lines: list[str] = field(default_factory=list)


@dataclass
class IndexEntry:
    """A `⬜ 2. The claim spine` line from a doc's own progress index."""

    part_no: int
    label: str
    marker: str
    line: int
    anchor: str             # anchor of the enclosing section


@dataclass
class Doc:
    path: Path
    rel: str                # "vault/knowledge/initiatives/hre-mvp-scope.md"
    frontmatter: dict
    sections: list[Section] = field(default_factory=list)
    index_entries: list[IndexEntry] = field(default_factory=list)


def _vault_root() -> Path:
    """Vault location. `AOS_VAULT_DIR` overrides it (tests, alternate hosts)."""
    env = os.environ.get("AOS_VAULT_DIR")
    return Path(env).expanduser() if env else Path.home() / "vault"


def _rel_path(path: Path) -> str:
    """Vault-relative path: `vault/knowledge/initiatives/hre-mvp-scope.md`."""
    for base in (_vault_root().parent, Path.home()):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def _strip_markers(text: str) -> str:
    return _MARKER_RE.sub("", text).strip()


def _parse_frontmatter(lines: list[str]) -> tuple[dict, int]:
    """Return (frontmatter dict, index of the first body line)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            block = "\n".join(lines[1:i])
            data = {}
            if _yaml is not None:
                try:
                    loaded = _yaml.safe_load(block)
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
            if not data:  # yaml missing or unparseable — take the flat keys
                for raw in block.splitlines():
                    if ":" in raw and not raw.startswith(" "):
                        k, v = raw.split(":", 1)
                        data[k.strip()] = v.strip()
            return data, i + 1
    return {}, 0


def parse_doc(path: Path) -> Doc:
    """Parse a markdown doc into sections and index entries.

    Fenced code blocks are tracked so a `#` inside a snippet is never mistaken
    for a heading — but index lines *are* read inside fences, because that is
    exactly where progress indexes tend to live.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    frontmatter, start = _parse_frontmatter(lines)

    doc = Doc(path=path, rel=_rel_path(path), frontmatter=frontmatter)
    in_fence = False
    current: Section | None = None
    stack: list[Section] = []

    for idx in range(start, len(lines)):
        raw = lines[idx]
        lineno = idx + 1

        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            # Fence lines go to every open section, so each one can track
            # fence state independently when its prose is extracted.
            for section in stack:
                section.lines.append(raw)
            continue

        m = _HEADING_RE.match(raw) if not in_fence else None
        if m:
            level = len(m.group(1))
            raw_heading = m.group(2).strip()
            marker_m = _MARKER_RE.search(raw_heading)
            heading = _strip_markers(raw_heading)
            anchor_m = _ANCHOR_RE.match(heading)
            anchor = anchor_m.group(1) if anchor_m else _slug(heading)
            part_m = _PART_RE.search(heading)
            section = Section(
                level=level,
                heading=heading,
                raw_heading=raw_heading,
                anchor=anchor,
                line=lineno,
                part_no=int(part_m.group(1)) if part_m else None,
                marker=marker_m.group(0) if marker_m else None,
            )
            doc.sections.append(section)
            while stack and stack[-1].level >= level:
                stack.pop()
            # Ancestors keep the subheading line: a `## Part 0` section needs to
            # see its own `### Still to do` marker to find the criteria under it.
            for ancestor in stack:
                ancestor.lines.append(raw)
            stack.append(section)
            current = section
            continue

        idx_m = _INDEX_RE.match(raw)
        if idx_m:
            doc.index_entries.append(
                IndexEntry(
                    part_no=int(idx_m.group("num")),
                    label=_strip_markers(idx_m.group("label")),
                    marker=idx_m.group("marker"),
                    line=lineno,
                    anchor=current.anchor if current else _slug(path.stem),
                )
            )

        # A line belongs to every open section, so a `## Part 2` section owns
        # the prose sitting under its own `### 4C.1` subheadings too.
        for section in stack:
            section.lines.append(raw)

    return doc


# ── Body extraction ─────────────────────────────────────────────────────

def _is_prose(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.startswith(("#", "|", ">", "```", "~~~")):
        return False
    if re.match(r"^\s*[-*+]\s", line) or re.match(r"^\s*\d+[.)]\s", line):
        return False
    if re.match(r"^[-*_=]{3,}$", s):          # horizontal rule
        return False
    if re.match(r"^!?\[.*\]\(.*\)$", s):      # bare image / link line
        return False
    if _INDEX_RE.match(line):
        return False
    return True


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split section content into ordered ("para" | "table" | "list", lines).

    Headings, fenced code, blockquotes, rules and index lines break blocks and
    are never content themselves.
    """
    out: list[tuple[str, list[str]]] = []
    kind: str | None = None
    buf: list[str] = []
    in_fence = False

    def flush():
        nonlocal kind, buf
        if kind and buf:
            out.append((kind, buf))
        kind, buf = None, []

    for raw in lines:
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        s = raw.strip()
        if (not s or s.startswith(("#", ">"))
                or re.match(r"^[-*_=]{3,}$", s)
                or _INDEX_RE.match(raw)):
            flush()
            continue
        if s.startswith("|"):
            this = "table"
        elif re.match(r"^\s*[-*+]\s", raw) or re.match(r"^\s*\d+[.)]\s", raw):
            this = "list"
        elif _is_prose(raw):
            this = "para"
        else:
            flush()
            continue
        if this != kind:
            flush()
            kind = this
        buf.append(raw)
    flush()
    return out


def _paragraph_text(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(l.strip() for l in lines)).strip()


def _describes_work(text: str) -> bool:
    """Is this paragraph a description, or just scaffolding?

    Rejected: anything too short to say much, a lead-in ending in a colon
    (whatever it introduces is a table or list, not this sentence), and
    see-also pointers at another document.
    """
    if len(text) < MIN_BODY_CHARS or len(text.split()) < MIN_BODY_WORDS:
        return False
    if text.rstrip().endswith(":"):
        return False
    if _POINTER_RE.match(text) and (".md" in text or "](" in text or "http" in text):
        return False
    return True


def _render_table(lines: list[str]) -> str:
    """A markdown table as verbatim bullets. Cells are never rewritten."""
    rows = []
    for raw in lines:
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue                       # the |---|---| separator
        rows.append([c for c in cells])
    if len(rows) < 2:                      # header + at least one data row
        return ""
    out = []
    for cells in rows[1:1 + MAX_STRUCTURED_ROWS]:
        head = cells[0].strip("*").strip()      # the cell may be bold already
        rest = [c for c in cells[1:] if c]
        if not head or not rest:
            continue
        out.append(f"- **{head}** — {' · '.join(rest)}")
    if len(rows) - 1 > MAX_STRUCTURED_ROWS:
        out.append(f"- …and {len(rows) - 1 - MAX_STRUCTURED_ROWS} more")
    return "\n".join(out) if len(out) >= 2 else ""


def _render_list(lines: list[str]) -> str:
    items = []
    for raw in lines:
        m = _BULLET_RE.match(raw) or re.match(r"^\s*\d+[.)]\s+(.+?)\s*$", raw)
        if m:
            items.append(f"- {m.group(1).strip()}")
    if len(items) < 2:
        return ""
    if len(items) > MAX_STRUCTURED_ROWS:
        extra = len(items) - MAX_STRUCTURED_ROWS
        items = items[:MAX_STRUCTURED_ROWS] + [f"- …and {extra} more"]
    return "\n".join(items)


def _first_body(section: Section) -> str:
    """The first block in the section that actually describes the work.

    Document order decides: whichever qualifying block comes first wins. So a
    section that opens with real prose gives you that prose, and a section
    whose substance is a decision table (`## 4A · Part 1`, whose only prose is
    "Full detail: …. The decisions:") gives you the table rather than a stub.
    Everything is verbatim — cells and bullets are moved, never reworded.
    """
    for kind, lines in _blocks(section.lines):
        if kind == "para":
            text = _paragraph_text(lines)
            if _describes_work(text):
                return text
        elif kind == "table":
            rendered = _render_table(lines)
            if rendered:
                return rendered
        elif kind == "list":
            rendered = _render_list(lines)
            if rendered:
                return rendered
    return ""


def _acceptance(section: Section) -> list[str]:
    """Checklist items, or bullets under an acceptance-flavoured subheading."""
    items: list[str] = []
    for raw in section.lines:
        m = _CHECKBOX_RE.match(raw)
        if m:
            items.append(m.group(2).strip())
    if items:
        return items[:MAX_ACCEPTANCE_ITEMS]

    collecting = bool(_ACCEPTANCE_RE.search(section.heading))
    in_fence = False
    for raw in section.lines:
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        h = _HEADING_RE.match(raw)
        if h:
            collecting = bool(_ACCEPTANCE_RE.search(h.group(2)))
            continue
        if collecting:
            b = _BULLET_RE.match(raw)
            if b:
                items.append(b.group(1).strip())
    return items[:MAX_ACCEPTANCE_ITEMS]


# ── Doc discovery ───────────────────────────────────────────────────────

def _initiative_key(project: dict) -> str | None:
    desc = project.get("description") or ""
    m = re.search(r"initiative:\s*([A-Za-z0-9._-]+)", desc)
    return m.group(1) if m else None


def find_project_docs(project_id: str, initiative: str | None = None) -> list[Doc]:
    """Source docs for a project, most-authoritative first.

    A doc qualifies if its filename is the initiative slug, is the project id
    or carries it as a `<project>-` prefix, or its frontmatter names the
    project. Ordering is derived, not hand-tuned: a doc that another doc
    declares it supersedes sinks below the one superseding it, then newest
    frontmatter `date` wins, then path order for stability.
    """
    root = _vault_root() / "knowledge"
    if not root.is_dir():
        return []

    docs: list[Doc] = []
    for path in sorted(root.rglob("*.md")):
        stem = path.stem
        by_name = (
            stem == project_id
            or stem.startswith(f"{project_id}-")
            or (initiative is not None and stem == initiative)
        )
        try:
            doc = parse_doc(path) if by_name else None
            if doc is None:
                # Cheap frontmatter-only read for everything else.
                head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]
                fm, _ = _parse_frontmatter(head)
                if str(fm.get("project") or "") != project_id:
                    continue
                doc = parse_doc(path)
        except OSError:
            continue
        docs.append(doc)

    superseded: set[str] = set()
    for doc in docs:
        for key in ("supersedes", "supersedes_partially", "superseded_by"):
            val = doc.frontmatter.get(key)
            if key == "superseded_by":
                if val:
                    superseded.add(doc.path.name)
                continue
            if isinstance(val, str):
                val = [val]
            for name in val or []:
                superseded.add(str(name).strip())

    def rank(doc: Doc) -> tuple:
        return (
            1 if doc.path.name in superseded else 0,
            _neg_date(doc.frontmatter.get("date")),
            doc.rel,
        )

    return sorted(docs, key=rank)


def _neg_date(value) -> str:
    """Sort key that puts newer dates first (string-reversed ISO dates)."""
    s = str(value or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return "0000-00-00"
    # invert digits so ascending sort == newest first
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in m.group(0))


# ── Matching ────────────────────────────────────────────────────────────

def _title_tokens(title: str) -> set[str]:
    # Drop a leading project prefix ("HRE: vertical slice") and any "Part N:".
    cleaned = re.sub(r"^[A-Za-z]{2,6}\s*[:—-]\s*", "", title)
    cleaned = _PART_RE.sub(" ", cleaned)
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def match_section(title: str, docs: list[Doc]) -> Section | None:
    """Find the section a task title refers to. Doc order breaks ties.

    Primary key is the `Part N` token — the section-number prefixes in these
    docs are irregular (`4 ·`, `4A ·`, `4C ·`), so prefix ordering means
    nothing and only the part number is reliable. The fallback is a
    deliberately strict title-token overlap.
    """
    part_m = _PART_RE.search(title)
    if part_m:
        want = int(part_m.group(1))
        for doc in docs:
            for section in doc.sections:
                if section.part_no == want:
                    return section
        return None

    tokens = _title_tokens(title)
    if len(tokens) < FALLBACK_MIN_SHARED:
        return None
    best: tuple[float, Section] | None = None
    for doc in docs:
        for section in doc.sections:
            if section.level > 3:
                continue
            shared = tokens & _title_tokens(section.heading)
            if len(shared) < FALLBACK_MIN_SHARED:
                continue
            ratio = len(shared) / len(tokens)
            if ratio < FALLBACK_MIN_RATIO:
                continue
            if best is None or ratio > best[0]:
                best = (ratio, section)
    return best[1] if best else None


def _anchor_ref(doc_rel: str, anchor: str) -> str:
    return f"{doc_rel}#{anchor}"


def _find_doc(docs: list[Doc], section: Section) -> Doc | None:
    for doc in docs:
        if any(s is section for s in doc.sections):
            return doc
    return None


# ── Status disagreement ─────────────────────────────────────────────────

def _doc_markers(docs: list[Doc], part_no: int) -> dict[str, list[tuple[str, str, str]]]:
    """Every status marker any doc carries for one part.

    Returns {doc_rel: [(kind, anchor, marker), ...]} where kind is
    "heading" or "index".
    """
    found: dict[str, list[tuple[str, str, str]]] = {}
    for doc in docs:
        hits: list[tuple[str, str, str]] = []
        for section in doc.sections:
            if section.part_no == part_no and section.marker:
                hits.append(("heading", section.anchor, section.marker))
        for entry in doc.index_entries:
            if entry.part_no == part_no:
                hits.append(("index", entry.anchor, entry.marker))
        if hits:
            found[doc.rel] = hits
    return found


def _describe_where(kind: str, anchor: str) -> str:
    return f"the index in §{anchor}" if kind == "index" else f"the section heading (§{anchor})"


def detect_status_disagreements(task: dict, docs: list[Doc]) -> list[Conflict]:
    """Compare a task's status against every marker the docs carry for it.

    Two kinds of finding, both reported and neither resolved:
      * a doc contradicting *itself* (its index says one thing, its section
        heading another);
      * a doc contradicting the tracker.
    """
    title = task.get("title", "")
    part_m = _PART_RE.search(title)
    if not part_m:
        return []
    part_no = int(part_m.group(1))
    task_state = TASK_STATE.get(str(task.get("status", "")).lower())
    if task_state is None:      # cancelled, or something we don't model
        return []

    conflicts: list[Conflict] = []
    for doc_rel, hits in sorted(_doc_markers(docs, part_no).items()):
        by_state: dict[str, list[tuple[str, str, str]]] = {}
        for kind, anchor, marker in hits:
            by_state.setdefault(MARKERS[marker], []).append((kind, anchor, marker))

        if len(by_state) > 1:
            parts = []
            refs = []
            for state, entries in sorted(by_state.items()):
                for kind, anchor, marker in entries:
                    parts.append(
                        f"{_describe_where(kind, anchor)} marks it "
                        f"{MARKER_ENGLISH[state]} ({marker})"
                    )
                    refs.append(_anchor_ref(doc_rel, anchor))
            conflicts.append(Conflict(
                kind="status_disagreement",
                severity="warn",
                message=(
                    f"{Path(doc_rel).name} contradicts itself about "
                    f"\"{title}\": " + ", while ".join(parts) + "."
                ),
                refs=refs,
            ))

        for state, entries in sorted(by_state.items()):
            if state == task_state:
                continue
            refs = [task.get("id", "")]
            wheres = []
            for kind, anchor, marker in entries:
                refs.append(_anchor_ref(doc_rel, anchor))
                wheres.append(_describe_where(kind, anchor))
            conflicts.append(Conflict(
                kind="status_disagreement",
                severity="warn",
                message=(
                    f"\"{title}\" is marked {MARKER_ENGLISH[state]} in "
                    f"{Path(doc_rel).name} ({', '.join(wheres)}) but is "
                    f"{MARKER_ENGLISH[task_state]} in the tracker "
                    f"(status: {task.get('status')})."
                ),
                refs=refs,
            ))
    return conflicts


# ── Task body storage ───────────────────────────────────────────────────

PROVENANCE_KEYS = ("acceptance", "body_source", "body_synced_at", "body_hash")


def _fields(task: dict) -> dict:
    f = task.get("fields") or {}
    return f if isinstance(f, dict) else {}


def _description(task: dict) -> str:
    """The raw description. backend surfaces the column as `notes`."""
    return task.get("notes") or task.get("description") or ""


def _split_description(text: str) -> tuple[str, str]:
    """(prose, meta comment). `<!-- meta: source_ref:… -->` is not a body."""
    meta = ""
    m = _META_RE.search(text or "")
    if m:
        meta = m.group(0)
    prose = _META_RE.sub("", text or "").strip()
    return prose, meta


def _body_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def task_body(task: dict) -> dict:
    """Read the enriched body off a task dict. Empty dict when never enriched."""
    prose, _ = _split_description(_description(task))
    fields = _fields(task)
    out = {k: fields[k] for k in PROVENANCE_KEYS if k in fields}
    if prose:
        out["body"] = prose
    return out


def _is_ours(task: dict) -> bool:
    """True when the current description is the one this module last wrote.

    Anything else — prose typed by the operator, notes left by another agent —
    is not ours to replace.
    """
    prose, _ = _split_description(_description(task))
    if not prose:
        return True
    return _fields(task).get("body_hash") == _body_hash(prose)


# ── The pass ────────────────────────────────────────────────────────────

def enrich_project(project_id: str, *, dry_run: bool = False) -> EnrichReport:
    """Pull task bodies for one project from its source docs.

    Matches each task to a doc section, pulls the first describing block into
    `tasks.description`, any checklist into `fields.acceptance`, and records
    the anchored source. Tasks with no matching section get a `no_body`
    conflict rather than invented text. Tasks that already carry a
    hand-written description are left untouched and listed in
    `report.skipped`. Status markers in the docs are compared against tracker
    status and any disagreement is reported, never resolved.

    With `dry_run=True` nothing is written — the same report comes back with
    `changed == 0` and the would-be payloads in `report.pulled`.
    """
    import backend as _backend

    projects = {p["id"]: p for p in _backend.get_all_projects()}
    project = projects.get(project_id)
    if project is None:
        raise ValueError(f"unknown project: {project_id!r}")

    docs = find_project_docs(project_id, _initiative_key(project))
    tasks = [t for t in _backend.get_all_tasks() if t.get("project") == project_id]
    tasks.sort(key=lambda t: _task_sort_key(t.get("id", "")))

    report = EnrichReport(project_id=project_id)
    now = datetime.now().isoformat(timespec="seconds")

    for task in tasks:
        task_id = task.get("id", "")
        title = task.get("title", "")

        report.disagreements.extend(detect_status_disagreements(task, docs))

        task_docs = _prioritise(docs, task.get("source_ref"))
        section = match_section(title, task_docs)
        doc = _find_doc(task_docs, section) if section else None
        body = _first_body(section) if section else ""

        if not section or not body:
            report.unmatched.append(task_id)
            reason = (
                "no matching section in the project's source docs"
                if not section else
                f"a section ({_anchor_ref(doc.rel, section.anchor)}) with nothing "
                f"in it that describes the work — only a pointer or scaffolding"
            )
            refs = [task_id]
            if section and doc:
                refs.append(_anchor_ref(doc.rel, section.anchor))
            report.conflicts.append(Conflict(
                kind="no_body",
                severity="warn",
                message=(
                    f"\"{title}\" has {reason} — left empty rather than "
                    f"filled with generated text."
                ),
                refs=refs,
            ))
            continue

        anchor = _anchor_ref(doc.rel, section.anchor)
        payload = {
            "body": body,
            "acceptance": _acceptance(section),
            "body_source": anchor,
            "body_synced_at": now,
        }
        report.matched.append((task_id, anchor))
        report.pulled[task_id] = payload

        prose, meta = _split_description(_description(task))
        if not _is_ours(task):
            # Someone wrote this by hand. The doc does not get to overwrite a
            # person — report the pull and move on.
            report.skipped.append((
                task_id,
                f"already has a hand-written description; {anchor} not applied",
            ))
            continue

        current = _fields(task)
        unchanged = (
            prose == body
            and current.get("body_source") == anchor
            and current.get("acceptance", []) == payload["acceptance"]
        )
        if unchanged:
            continue

        report.pending.append(task_id)
        if not dry_run:
            description = f"{body}\n\n{meta}" if meta else body
            fields = dict(current)
            fields.update({
                "acceptance": payload["acceptance"],
                "body_source": anchor,
                "body_synced_at": now,
                "body_hash": _body_hash(body),
            })
            _backend.update_task(task_id, description=description, fields=fields)
            report.changed += 1

    report.conflicts.extend(report.disagreements)
    return report


def _prioritise(docs: list[Doc], source_ref: str | None) -> list[Doc]:
    """Put a task's own `source_ref` doc first, if it names one.

    Some tasks carry `<!-- meta: source_ref:vault/... -->` in their
    description. That is the most direct statement of where a task came from,
    so it outranks the project-wide doc ordering for that task.
    """
    if not source_ref:
        return docs
    name = Path(str(source_ref)).name
    named = [d for d in docs if d.path.name == name]
    return named + [d for d in docs if d.path.name != name] if named else docs


def _task_sort_key(task_id: str):
    """Natural order: hre#1.2 before hre#1.10."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", task_id)]


# ── CLI ─────────────────────────────────────────────────────────────────

def render_report(report: EnrichReport) -> str:
    """Plain-text rendering of a report — what `work enrich` prints."""
    out = [f"enrich {report.project_id}"]
    out.append(f"  matched:   {len(report.matched)}")
    for task_id, anchor in report.matched:
        body = report.pulled.get(task_id, {}).get("body", "").replace("\n", " ")
        excerpt = (body[:100] + "…") if len(body) > 100 else body
        out.append(f"    {task_id:10s} -> {anchor}")
        out.append(f"      {excerpt}")

    # The gap is the finding: a task with no section is a doc that hasn't been
    # written yet, not a matcher that failed. Say which it is.
    reasons = {c.refs[0]: c.message for c in report.conflicts
               if c.kind == "no_body" and c.refs}
    out.append(f"  unmatched: {len(report.unmatched)} (nothing written in the source docs yet)")
    for task_id in report.unmatched:
        out.append(f"    {task_id:10s} {reasons.get(task_id, '')}")

    if report.skipped:
        out.append(f"  skipped:   {len(report.skipped)} (hand-written description left alone)")
        for task_id, why in report.skipped:
            out.append(f"    {task_id:10s} {why}")

    out.append(f"  conflicts: {len(report.conflicts)}")
    for c in report.conflicts:
        out.append(f"    [{c.kind}] {c.message}")
        out.append(f"      refs: {', '.join(c.refs)}")
    out.append(f"  changed:   {report.changed} written"
               + (f", {len(report.pending)} pending (dry run)" if report.pending
                  and not report.changed else ""))
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("usage: enrich.py <project_id> [--dry-run]")
        raise SystemExit(2)
    print(render_report(enrich_project(args[0], dry_run="--dry-run" in sys.argv)))
