"""
Migration 103: Strip legacy pre-marker sections from ~/.claude/CLAUDE.md.

Before the AOS:MANAGED block system existed, ~/.claude/CLAUDE.md carried
hand-written sections (## Rules, ## Quick Reference, ...). When managed blocks
shipped, the reconcile check correctly appended them WITHOUT touching user
content — leaving every pre-marker machine with two "## Rules" and two
"## Quick Reference" sections, loaded into every session forever. Confirmed on
both developer and operator machines (agents-mac-mini, faisal-mini).

The unique bullets the legacy Rules section carried (push-to-main approval,
no hardcoded lists, destructive-ops approval) were absorbed into the managed
rules block at version 4 in the same commit as this migration (atomic
migration rule). This migration then:

1. Refreshes the managed blocks first (so v4 content is in place).
2. Removes any unmanaged section whose heading duplicates a managed block's
   heading — but only once the managed counterpart is at or above the version
   that absorbed its content, so nothing is ever lost.

Legacy sections with no managed counterpart (Storage Architecture, Vault
Structure, Memory & Search, operator preferences) are NEVER touched.
"""

DESCRIPTION = "Dedupe legacy pre-marker sections in ~/.claude/CLAUDE.md"

import re
import sys
from pathlib import Path

TARGET = Path.home() / ".claude" / "CLAUDE.md"

BLOCK_RE = re.compile(
    r'<!-- AOS:MANAGED name="(?P<name>[^"]+)" version="(?P<version>\d+)" -->\n'
    r'(?P<content>.*?)'
    r'<!-- AOS:END -->',
    re.DOTALL,
)

# A legacy duplicate may only be stripped once the managed block that absorbed
# its content is at or above this version. Headings not listed default to 1
# (managed content was always a superset).
ABSORBED_AT = {"Rules": 4}

HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)
BOUNDARY_RE = re.compile(r"^## |^<!-- AOS:MANAGED", re.MULTILINE)


def _managed_state(text: str):
    """Spans of managed blocks + {heading: version} they declare."""
    spans = []
    headings = {}
    for m in BLOCK_RE.finditer(text):
        spans.append((m.start(), m.end()))
        h = HEADING_RE.search(m.group("content"))
        if h:
            headings[h.group(1).strip()] = int(m.group("version"))
    return spans, headings


def _legacy_dup_spans(text: str):
    """(start, end) spans of unmanaged sections that duplicate a managed heading."""
    spans, headings = _managed_state(text)

    def in_managed(i: int) -> bool:
        return any(s <= i < e for s, e in spans)

    dups = []
    for m in HEADING_RE.finditer(text):
        if in_managed(m.start()):
            continue
        title = m.group(1).strip()
        if title not in headings:
            continue
        if headings[title] < ABSORBED_AT.get(title, 1):
            continue  # managed counterpart not yet current — don't lose content
        boundary = BOUNDARY_RE.search(text, m.end())
        dups.append((m.start(), boundary.start() if boundary else len(text)))
    return dups


def check() -> bool:
    """Applied when no strippable legacy duplicate remains."""
    if not TARGET.exists():
        return True
    return not _legacy_dup_spans(TARGET.read_text())


def up() -> bool:
    if not TARGET.exists():
        print("  no ~/.claude/CLAUDE.md — nothing to do")
        return True

    # Refresh managed blocks FIRST so absorbed content (rules v4) is in place
    # before any legacy text is removed.
    try:
        checks_dir = Path.home() / "aos" / "core" / "infra" / "reconcile" / "checks"
        sys.path.insert(0, str(checks_dir))
        import claude_md  # noqa: E402

        claude_md.GlobalClaudeMdCheck().fix()
        print("  managed blocks refreshed")
    except Exception as e:  # non-fatal: version gate below still protects content
        print(f"  WARNING: managed-block refresh failed ({e}) — version gate applies")

    text = TARGET.read_text()
    dups = _legacy_dup_spans(text)
    if not dups:
        print("  no legacy duplicates found")
        return True

    removed = []
    for start, end in reversed(dups):
        removed.append(text[start:end].splitlines()[0].lstrip("# ").strip())
        text = text[:start] + text[end:]
    text = re.sub(r"\n{3,}", "\n\n", text)
    TARGET.write_text(text)
    print(f"  removed legacy duplicate sections: {', '.join(reversed(removed))}")
    return True
