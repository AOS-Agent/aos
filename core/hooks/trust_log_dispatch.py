#!/usr/bin/env python3
"""PostToolUse hook — automatic trust-log entries for catalog dispatch.

chief.md's dispatch mandate says every catalog dispatch gets logged:
`trust-log record <agent> <capability> <result> --action "..."`. Depending
on the dispatching agent to remember to run that CLI by hand never worked —
the dangling-wires audit (2026-09-13) found 5 rows had ever been written,
stale 54 days. This hook makes it automatic: whenever the `Agent` tool is
used to dispatch a CATALOG agent (anything under ~/.claude/agents/*.md whose
frontmatter `name:` is not chief/steward/advisor/onboard — those four are
system roles, not specialist dispatches), it writes an `executed` row via
the real trust-log CLI, so log format and scoring stay in one place
(core/bin/cli/trust-log).

Hook contract (matches core/hooks/mention_context.py): read hook input JSON
on stdin, print a small JSON object, exit 0. MUST NEVER fail or block the
tool call — any error is swallowed and the hook exits cleanly.

Discovery is filesystem-driven (glob ~/.claude/agents/*.md), never a
hardcoded agent list — lists drift, directories don't.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path.home() / ".claude" / "agents"

# The four system roles — dispatched internally, not "catalog dispatch" in
# the chief.md sense. Comparison is case-insensitive (frontmatter is
# lowercase; subagent_type as typed by callers may not always match case).
CORE_AGENTS = {"chief", "steward", "advisor", "onboard"}

# core/bin/cli/trust-log, resolved relative to this file so it works whether
# this hook runs from ~/aos (symlinked release) or a dev worktree — never a
# hardcoded ~/aos path.
TRUST_LOG_CLI = Path(__file__).resolve().parent.parent / "bin" / "cli" / "trust-log"

# Frontmatter is simple single-line YAML scalars (name:, role:, description:)
# for every agent file this hook cares about — a full YAML parse is not worth
# the import weight or the risk of choking on a multi-line block this hook
# doesn't need. Matches "key: value" or "key: \"value\"" per line.
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?\r?\n)---", re.DOTALL)
_FIELD_RE = re.compile(r'^([A-Za-z_][\w-]*):\s*(.*?)\s*$', re.MULTILINE)


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fields: dict[str, str] = {}
    for fm in _FIELD_RE.finditer(m.group(1)):
        key, val = fm.group(1), fm.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key not in fields:  # first line wins; ignore nested list bodies
            fields[key] = val
    return fields


def discover_catalog_agents(agents_dir: Path = AGENTS_DIR) -> dict[str, str]:
    """Return {agent_name: capability} for every catalog agent.

    `capability` is the frontmatter's `role:` field when declared, else the
    generic 'dispatch' — there is no per-agent capability taxonomy today, and
    trust-log's own scoring just groups by whatever string it's given.
    """
    catalog: dict[str, str] = {}
    if not agents_dir.is_dir():
        return catalog
    for path in sorted(agents_dir.glob("*.md")):
        try:
            text = path.read_text()
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        name = fm.get("name")
        if not name or name.lower() in CORE_AGENTS:
            continue
        catalog[name] = fm.get("role") or "dispatch"
    return catalog


def _record_dispatch(agent: str, capability: str, action: str, session_id: str) -> None:
    """Shell out to the real trust-log CLI — same code path as
    codex-dispatch's ledger write-back, so scoring/weighting logic lives in
    exactly one place. Benchmarked ~25ms; well under the hook's budget.
    Never allowed to raise or block the tool call."""
    try:
        subprocess.run(
            [sys.executable, str(TRUST_LOG_CLI), "record", agent, capability,
             "executed", "--action", action, "--session", session_id or ""],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        hook_input = {}

    try:
        if hook_input.get("tool_name") == "Agent":
            tool_input = hook_input.get("tool_input") or {}
            subagent_type = (tool_input.get("subagent_type") or "").strip()
            if subagent_type:
                catalog = discover_catalog_agents()
                match = next(
                    (name for name in catalog if name.lower() == subagent_type.lower()),
                    None,
                )
                if match is not None:
                    action = (
                        tool_input.get("description")
                        or tool_input.get("prompt")
                        or ""
                    )[:80]
                    session_id = hook_input.get("session_id", "")
                    _record_dispatch(match, catalog[match], action, session_id)
    except Exception:
        pass  # never let logging the dispatch block the dispatch itself

    print(json.dumps({}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({}))
    sys.exit(0)
