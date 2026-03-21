"""
Migration 011: Bootstrap Claude Code settings with AOS defaults.

A fresh install needs:
- agent: chief (so Chief is the default agent)
- env vars for agent teams
- PostCompact hook (re-inject work context after compaction)

Migration 005 only wires SessionStart/SessionEnd hooks.
This fills in the rest of the base config a new user needs.

Existing settings are preserved — we only add what's missing.
"""

DESCRIPTION = "Bootstrap Claude Code settings (agent, env, base config)"

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# Base config that AOS needs
BASE_CONFIG = {
    "agent": "chief",
    "env": {
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        "CLAUDE_CODE_TEAMMATE_MODE": "in-process",
    },
}

# PostCompact hook — re-injects work context after context compaction
POSTCOMPACT_HOOK = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": f"python3 {Path.home()}/aos/core/work/inject_context.py",
        }
    ],
}


def _get_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def _save_settings(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def check() -> bool:
    """Applied if agent is set and env vars exist."""
    settings = _get_settings()
    if settings.get("agent") != "chief":
        return False
    env = settings.get("env", {})
    if "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env:
        return False
    # Check PostCompact hook exists
    hooks = settings.get("hooks", {})
    postcompact = hooks.get("PostCompact", [])
    if not postcompact:
        return False
    return True


def up() -> bool:
    """Set base config, preserving existing values."""
    settings = _get_settings()

    # Set agent if not already set
    if "agent" not in settings:
        settings["agent"] = BASE_CONFIG["agent"]
        print("       Set default agent: chief")
    else:
        print(f"       Agent already set: {settings['agent']}")

    # Merge env vars (don't overwrite existing)
    if "env" not in settings:
        settings["env"] = {}
    for key, val in BASE_CONFIG["env"].items():
        if key not in settings["env"]:
            settings["env"][key] = val
            print(f"       Set env: {key}")

    # Add PostCompact hook if missing
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "PostCompact" not in settings["hooks"] or not settings["hooks"]["PostCompact"]:
        settings["hooks"]["PostCompact"] = [POSTCOMPACT_HOOK]
        print("       Added PostCompact hook")
    else:
        print("       PostCompact hook already exists")

    _save_settings(settings)
    return True
