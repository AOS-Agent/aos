"""
Migration 005: Register AOS hooks in Claude Code settings.

Hooks:
- SessionStart: inject_context.py (loads active tasks/threads into session)
- SessionEnd: session_close.py (links session to tasks, captures outcomes)

These are registered in ~/.claude/settings.json.
Existing hooks are preserved — AOS hooks are appended.
"""

DESCRIPTION = "Wire work system hooks into Claude Code"

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
AOS_DIR = Path.home() / "aos"

HOOKS = {
    "SessionStart": {
        "command": "python3 ~/aos/core/work/inject_context.py",
        "statusMessage": "Loading work context...",
        "description": "AOS: inject active tasks and threads",
    },
    "SessionEnd": {
        "command": "python3 ~/aos/core/work/session_close.py",
        "async": True,
        "description": "AOS: link session to tasks and capture outcomes",
    },
}


def _get_settings() -> dict:
    """Load existing settings or empty dict."""
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def _save_settings(data: dict):
    """Save settings preserving formatting."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _hook_installed(settings: dict, event: str, command: str) -> bool:
    """Check if a specific hook command is already registered.

    Handles both flat format (command directly in list) and nested format
    ({"hooks": [{"type": "command", "command": "..."}]}).
    """
    hooks = settings.get("hooks", {})
    event_hooks = hooks.get(event, [])
    if not isinstance(event_hooks, list):
        return False
    for h in event_hooks:
        # Flat format: {"command": "..."}
        if isinstance(h, dict) and h.get("command") == command:
            return True
        # String format
        if isinstance(h, str) and h == command:
            return True
        # Nested format: {"hooks": [{"command": "..."}]}
        if isinstance(h, dict) and "hooks" in h:
            for inner in h["hooks"]:
                if isinstance(inner, dict) and inner.get("command") == command:
                    return True
    return False


def check() -> bool:
    """Applied if all AOS hooks are registered."""
    settings = _get_settings()
    for event, hook in HOOKS.items():
        if not _hook_installed(settings, event, hook["command"]):
            return False
    return True


def up() -> bool:
    """Register AOS hooks in settings.json."""
    settings = _get_settings()

    if "hooks" not in settings:
        settings["hooks"] = {}

    for event, hook in HOOKS.items():
        if _hook_installed(settings, event, hook["command"]):
            print(f"       {event} hook already registered ✓")
            continue

        if event not in settings["hooks"]:
            settings["hooks"][event] = []

        hook_entry = {"type": "command", "command": hook["command"]}
        if hook.get("statusMessage"):
            hook_entry["statusMessage"] = hook["statusMessage"]
        if hook.get("async"):
            hook_entry["async"] = True
        settings["hooks"][event].append({
            "hooks": [hook_entry],
        })
        print(f"       Registered {event} → {hook['description']}")

    _save_settings(settings)
    return True
