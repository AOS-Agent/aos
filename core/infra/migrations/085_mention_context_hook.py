"""
Migration 085: Register the ambient mention-context hook (Phase 5).

Registers a UserPromptSubmit hook in ~/.claude/settings.json that injects a
person's cached mini-profile when the operator's prompt names them (last
interaction, open commitments both ways, their unanswered questions, recent
topics). The hook (core/hooks/mention_context.py) reads only pre-built JSON
snapshots — no DB, no model — so it stays well under its <100ms budget.

Atomic-migration rule: the hook file ships in the framework, but the instance
layer (~/.claude/settings.json) must be told about it, so that wiring lands in
the same change as the code. Existing hooks are preserved — this is appended and
is idempotent (a re-run detects the command and skips).
"""

DESCRIPTION = "Register UserPromptSubmit mention-context hook (Phase 5)"

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

EVENT = "UserPromptSubmit"
COMMAND = "python3 ~/aos/core/hooks/mention_context.py"


def _get_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _hook_installed(settings: dict, event: str, command: str) -> bool:
    event_hooks = settings.get("hooks", {}).get(event, [])
    if not isinstance(event_hooks, list):
        return False
    for h in event_hooks:
        if isinstance(h, dict) and h.get("command") == command:
            return True
        if isinstance(h, str) and h == command:
            return True
        if isinstance(h, dict) and "hooks" in h:
            for inner in h["hooks"]:
                if isinstance(inner, dict) and inner.get("command") == command:
                    return True
    return False


def check() -> bool:
    return _hook_installed(_get_settings(), EVENT, COMMAND)


def up() -> bool:
    settings = _get_settings()
    settings.setdefault("hooks", {})
    if _hook_installed(settings, EVENT, COMMAND):
        print(f"       {EVENT} mention-context hook already registered ✓")
        return True
    settings["hooks"].setdefault(EVENT, [])
    settings["hooks"][EVENT].append({
        "hooks": [{
            "type": "command",
            "command": COMMAND,
            "statusMessage": "Checking mentioned people...",
        }],
    })
    _save_settings(settings)
    print(f"       Registered {EVENT} → ambient mention-context hook")
    return True


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
