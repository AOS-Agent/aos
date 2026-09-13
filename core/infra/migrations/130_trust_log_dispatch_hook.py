"""
Migration 130: Register the automatic trust-log-dispatch PostToolUse hook
(aos#236.4).

chief.md's catalog-dispatch mandate ("log every catalog dispatch") had no
enforcement — the dangling-wires audit (2026-09-13) found 5 trust-log rows
had ever been written, stale 54 days. core/hooks/trust_log_dispatch.py makes
the write automatic: a PostToolUse hook, scoped to the Agent tool via
`matcher`, that writes a `record ... executed` row whenever a catalog agent
(anything in ~/.claude/agents/*.md that isn't chief/steward/advisor/onboard)
is dispatched.

Atomic-migration rule: the hook file ships in the framework, but the
instance layer (~/.claude/settings.json) must be told about it in the same
change. Existing hooks are preserved — this only appends, and is idempotent
(a re-run detects the command and skips). core/infra/reconcile/checks/hooks.py
also carries this in its REQUIRED_HOOKS so drift self-heals on every update
cycle; this migration is what wires it in for installs that predate that
check knowing about it — same two-tier pattern as migration 085
(mention_context UserPromptSubmit hook).
"""

DESCRIPTION = "Register PostToolUse trust-log-dispatch hook (aos#236.4)"

import json
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _settings_file() -> Path:
    return Path.home() / ".claude" / "settings.json"


EVENT = "PostToolUse"
COMMAND = "python3 ~/aos/core/hooks/trust_log_dispatch.py"
MATCHER = "Agent"


def _get_settings() -> dict:
    if _settings_file().exists():
        with open(_settings_file()) as f:
            return json.load(f)
    return {}


def _save_settings(data: dict) -> None:
    _settings_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_settings_file(), "w") as f:
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
        print(f"       {EVENT} trust-log-dispatch hook already registered ✓")
        return True
    settings["hooks"].setdefault(EVENT, [])
    settings["hooks"][EVENT].append({
        "matcher": MATCHER,
        "hooks": [{
            "type": "command",
            "command": COMMAND,
            "async": True,
        }],
    })
    _save_settings(settings)
    print(f"       Registered {EVENT} (matcher={MATCHER}) → trust-log-dispatch hook")
    return True


def down() -> bool:
    # Not automated: the settings.json entry may have been hand-edited since.
    # Remove the PostToolUse block for trust_log_dispatch.py manually if ever
    # needed — same posture as migration 085.
    return False


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
