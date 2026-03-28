"""
Invariant: CLAUDE.md files have current AOS-managed sections.

AOS owns specific sections marked with HTML comments:
    <!-- AOS:MANAGED name="section-name" version="N" -->
    ...content...
    <!-- AOS:END -->

User content outside these markers is NEVER touched.
When AOS ships updated content, it bumps the version number.
Reconcile replaces only outdated blocks.

Files that don't have markers yet get sections appended (not overwritten).
"""

import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


# Regex to find managed blocks
BLOCK_RE = re.compile(
    r'<!-- AOS:MANAGED name="(?P<name>[^"]+)" version="(?P<version>\d+)" -->\n'
    r'(?P<content>.*?)'
    r'<!-- AOS:END -->',
    re.DOTALL,
)


def _wrap(name: str, version: int, content: str) -> str:
    """Wrap content in AOS managed markers."""
    c = content.strip()
    return f'<!-- AOS:MANAGED name="{name}" version="{version}" -->\n{c}\n<!-- AOS:END -->'


def _find_block(text: str, name: str) -> re.Match | None:
    """Find a named managed block in text."""
    for m in BLOCK_RE.finditer(text):
        if m.group("name") == name:
            return m
    return None


def _check_sections(filepath: Path, sections: dict) -> bool:
    """Check if all managed sections are present and current."""
    if not filepath.exists():
        return False
    text = filepath.read_text()
    for name, (version, _) in sections.items():
        m = _find_block(text, name)
        if m is None:
            return False
        if int(m.group("version")) < version:
            return False
    return True


def _fix_sections(filepath: Path, sections: dict, header: str) -> CheckResult:
    """Update managed sections in a file, preserving user content."""
    check_name = filepath.name

    if not filepath.exists():
        # Fresh file — write header + all sections
        parts = [header]
        for name, (version, content) in sections.items():
            parts.append(_wrap(name, version, content))
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("\n\n".join(parts) + "\n")
        return CheckResult(
            check_name, Status.FIXED,
            f"Created {filepath} with {len(sections)} managed sections"
        )

    text = filepath.read_text()
    updated = []

    for name, (version, content) in sections.items():
        block = _wrap(name, version, content)
        m = _find_block(text, name)

        if m is None:
            # Section missing — append before any user content at the end
            text = text.rstrip() + "\n\n" + block + "\n"
            updated.append(f"added:{name}")
        elif int(m.group("version")) < version:
            # Section outdated — replace just this block
            text = text[:m.start()] + block + text[m.end():]
            updated.append(f"updated:{name}@v{version}")

    if updated:
        filepath.write_text(text)
        return CheckResult(
            check_name, Status.FIXED,
            f"Updated {filepath.name}: {', '.join(updated)}"
        )
    return CheckResult(check_name, Status.OK, "ok")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ~/CLAUDE.md — Root context file
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROOT_HEADER = "# AOS — Agentic Operating System\n\nThis Mac Mini runs AOS. The operating system lives at `~/aos/`."

# ~/CLAUDE.md is user-managed — no managed sections.
# Content is maintained directly. Storage layout, quick reference, and rules
# are already in the file without AOS:MANAGED markers.
# The reconcile check still ensures the file exists with a valid header.
ROOT_SECTIONS = {}


class RootClaudeMdCheck(ReconcileCheck):
    name = "root_claude_md"
    description = "~/CLAUDE.md managed sections are current"

    target = Path.home() / "CLAUDE.md"

    def check(self) -> bool:
        return _check_sections(self.target, ROOT_SECTIONS)

    def fix(self) -> CheckResult:
        return _fix_sections(self.target, ROOT_SECTIONS, ROOT_HEADER)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ~/.claude/CLAUDE.md — Global kernel (loaded every session)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GLOBAL_HEADER = "# AOS — Agentic Operating System\n\nThis machine runs AOS. Every session operates within this context."

GLOBAL_SECTIONS = {
    "boundaries": (2, """\
## Boundaries

```
INTERNAL:  ~/aos/ (system), ~/.aos/ (instance data)
AOS-X:     ~/vault/, ~/project/, ~/.cache/, ~/Library/Developer/ (all symlinked)
```"""),

    "agents": (1, """\
## Agents

| Agent | Role |
|-------|------|
| **Chief** | Orchestrator. Receives all requests. Delegates or acts directly. |
| **Steward** | System health, self-correction, maintenance. |
| **Advisor** | Analysis, knowledge curation, work planning, reviews. |

Additional agents activated from catalog or created by user."""),

    "skills": (1, """\
## Skills

Skills at `~/.claude/skills/`. Each has `SKILL.md` with trigger phrases.
When a request matches, load and follow the skill's protocol."""),

    "rules": (2, """\
## Rules

- **NEVER edit `~/aos/` directly.** All framework changes go in `~/project/aos/` (dev workspace). Commit and push from there. Runtime pulls on next update. Only `~/.aos/` and `~/.claude/` are edited directly.
- Secrets: macOS Keychain only (`agent-secret get/set`). Never in files.
- Network: localhost only. Tailscale for remote access.
- Questions: one at a time, never batch.
- Research first: check vault, config, and available data before asking.
- Delegate: dispatch to specialist agents for domain work."""),

    "quick-reference": (3, """\
## Quick Reference

- Operator profile: ~/.aos/config/operator.yaml
- Config: ~/aos/config/
- User data: ~/.aos/
- Vault search: `qmd query "<topic>" -n 5` or via QMD MCP tools
- Secrets: `~/aos/core/bin/agent-secret get/set`
- Memory index: `qmd status` (5 collections: log, knowledge, skills, agents, aos-docs)
- Claude Code harness: `~/.claude/settings.json` (permissions, hooks, agent, chrome)
- Claude Code preferences: `~/.claude.json` (remote control, UI toggles — set via `/config`)

**Claude Code config rule:** Never assume a setting key exists. If unsure, check docs at `code.claude.com/docs/en/settings` or toggle via `/config` and diff the file. See `rules/claude-code-config.md` for verified keys."""),
}


class GlobalClaudeMdCheck(ReconcileCheck):
    name = "global_claude_md"
    description = "~/.claude/CLAUDE.md managed sections are current"

    target = Path.home() / ".claude" / "CLAUDE.md"

    def check(self) -> bool:
        return _check_sections(self.target, GLOBAL_SECTIONS)

    def fix(self) -> CheckResult:
        return _fix_sections(self.target, GLOBAL_SECTIONS, GLOBAL_HEADER)
