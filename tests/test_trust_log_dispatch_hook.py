"""
core/hooks/trust_log_dispatch.py — automatic trust-log entries for catalog
dispatch (aos#236.4).

chief.md says every catalog dispatch must be logged
(`trust-log record <agent> <capability> <result>`), but the audit
(2026-09-13) found only 5 rows had ever been written, stale 54 days —
the mandate depended on the dispatching agent remembering to run the CLI
by hand every single time, and it doesn't happen.

This is a PostToolUse hook: when the tool is `Agent` and its `subagent_type`
is a catalog agent (any ~/.claude/agents/*.md whose frontmatter `name:` is
not chief/steward/advisor/onboard — those four are system roles, not
catalog dispatches), it appends a `record ... executed` row automatically,
via the real `trust-log` CLI (core/bin/cli/trust-log) so the log format and
scoring stay in one place. Follows core/hooks/mention_context.py's contract:
read hook input JSON on stdin, never raise, never block the caller.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "core" / "hooks" / "trust_log_dispatch.py"


def _load_hook_fresh():
    """Fresh module load so Path.home()-derived constants (AGENTS_DIR,
    TRUST_LOG_DIR if any) can be repointed before main() runs — same
    technique as test_session_close_threads.py / test_stop_hook_reconcile.py."""
    spec = importlib.util.spec_from_file_location("trust_log_dispatch_test", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHIEF_MD = '---\nname: chief\ndescription: "orchestrator"\ntools: "*"\n---\n\nChief.\n'
STEWARD_MD = '---\nname: steward\ndescription: "health"\nmodel: haiku\n---\n\nSteward.\n'
ADVISOR_MD = '---\nname: advisor\ndescription: "analysis"\nmodel: sonnet\n---\n\nAdvisor.\n'
ONBOARD_MD = '---\nname: onboard\ndescription: "setup"\nrole: Onboarding\n---\n\nOnboard.\n'
REVERSER_MD = (
    '---\nname: reverser\ndescription: "reverse engineering"\n'
    'role: Reverse Engineer\nmodel: opus\n---\n\nReverser.\n'
)
SENTINEL_MD = (
    '---\nname: Sentinel\ndescription: "autonomous commitments"\nmodel: sonnet\n---\n\nSentinel.\n'
)


def _make_agents_dir(root: Path) -> Path:
    agents_dir = root / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "chief.md").write_text(CHIEF_MD)
    (agents_dir / "steward.md").write_text(STEWARD_MD)
    (agents_dir / "advisor.md").write_text(ADVISOR_MD)
    (agents_dir / "onboard.md").write_text(ONBOARD_MD)
    (agents_dir / "reverser.md").write_text(REVERSER_MD)
    (agents_dir / "Sentinel.md").write_text(SENTINEL_MD)
    return agents_dir


def test_discover_catalog_agents_excludes_the_core_four(tmp_path):
    agents_dir = _make_agents_dir(tmp_path)
    mod = _load_hook_fresh()
    catalog = mod.discover_catalog_agents(agents_dir)
    assert set(catalog) == {"reverser", "Sentinel"}, catalog
    assert catalog["reverser"] == "Reverse Engineer"
    assert catalog["Sentinel"] == "dispatch"  # no role: field -> fallback


def _run_hook(mod, hook_input: dict) -> None:
    import contextlib

    with contextlib.redirect_stdout(io.StringIO()):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(hook_input))
        try:
            mod.main()
        finally:
            sys.stdin = old_stdin


def _trust_log_rows(home: Path) -> list[dict]:
    trust_dir = home / ".aos" / "logs" / "trust"
    rows = []
    if not trust_dir.is_dir():
        return rows
    for f in sorted(trust_dir.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_agent_dispatch_to_catalog_agent_writes_trust_log_row(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents_dir = _make_agents_dir(tmp_path)

    mod = _load_hook_fresh()
    monkeypatch.setattr(mod, "AGENTS_DIR", agents_dir)

    hook_input = {
        "session_id": "sess-reverser-1",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "reverser",
            "description": "extract design system",
            "prompt": "Reverse engineer example.com's design tokens.",
        },
        "tool_response": {"ok": True},
    }
    _run_hook(mod, hook_input)

    rows = _trust_log_rows(tmp_path)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["agent"] == "reverser"
    assert row["capability"] == "Reverse Engineer"
    assert row["result"] == "executed"
    assert "extract design system" in row["action"]
    assert row["session"] == "sess-reverser-1"


def test_dispatch_to_a_core_agent_is_not_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents_dir = _make_agents_dir(tmp_path)

    mod = _load_hook_fresh()
    monkeypatch.setattr(mod, "AGENTS_DIR", agents_dir)

    hook_input = {
        "session_id": "sess-chief-1",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "chief", "description": "internal dispatch"},
        "tool_response": {"ok": True},
    }
    _run_hook(mod, hook_input)

    assert _trust_log_rows(tmp_path) == []


def test_non_agent_tool_call_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents_dir = _make_agents_dir(tmp_path)

    mod = _load_hook_fresh()
    monkeypatch.setattr(mod, "AGENTS_DIR", agents_dir)

    hook_input = {
        "session_id": "sess-bash-1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {},
    }
    _run_hook(mod, hook_input)

    assert _trust_log_rows(tmp_path) == []


def test_unknown_subagent_type_is_not_logged(tmp_path, monkeypatch):
    """`fork`, `general-purpose`, and other built-in agent-tool types are not
    files under ~/.claude/agents/ — they must never match the catalog."""
    monkeypatch.setenv("HOME", str(tmp_path))
    agents_dir = _make_agents_dir(tmp_path)

    mod = _load_hook_fresh()
    monkeypatch.setattr(mod, "AGENTS_DIR", agents_dir)

    hook_input = {
        "session_id": "sess-fork-1",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "fork", "description": "fork myself"},
        "tool_response": {"ok": True},
    }
    _run_hook(mod, hook_input)

    assert _trust_log_rows(tmp_path) == []


def test_garbage_stdin_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    mod = _load_hook_fresh()

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("{not valid json::")
    try:
        mod.main()  # must not raise
    finally:
        sys.stdin = old_stdin
