"""
Invariant: settings.json has correct hooks, paths, and permissions.

Checks:
1. Hook commands reference valid ~/aos/ paths (not stale prefixes)
2. Required hooks are registered
3. Permissions include ~/.claude/** so agents can edit skills/config
   without being prompted (bypassPermissions doesn't cover harness files)
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class HooksPathCheck(ReconcileCheck):
    name = "settings_config"
    description = "settings.json hooks, paths, and permissions are correct"

    SETTINGS = Path.home() / ".claude" / "settings.json"

    # Old path prefixes that should be replaced
    STALE_PATTERNS = {
        "~/aosv2/": "~/aos/",
        "~/.aos-v2/": "~/aos/",
        "~/agent/": "~/aos/",
        "$HOME/aosv2/": "$HOME/aos/",
    }

    # Required hooks — if missing, add them
    REQUIRED_HOOKS = {
        "SessionStart": {
            "command": "python3 ~/aos/core/work/inject_context.py",
            "statusMessage": "Loading work context...",
        },
        "SessionEnd": {
            "command": "python3 ~/aos/core/work/session_close.py",
            "async": True,
        },
    }

    # Required permissions — bypassPermissions doesn't cover harness files
    REQUIRED_PERMISSIONS = [
        "Edit ~/.claude/**",
        "Write ~/.claude/**",
    ]

    def check(self) -> bool:
        if not self.SETTINGS.exists():
            return False

        text = self.SETTINGS.read_text()

        # Check for stale paths
        if any(pat in text for pat in self.STALE_PATTERNS):
            return False

        try:
            settings = json.loads(text)
        except json.JSONDecodeError:
            return False

        # Check required hooks exist
        hooks = settings.get("hooks", {})
        for event, spec in self.REQUIRED_HOOKS.items():
            if not self._hook_exists(hooks, event, spec["command"]):
                return False

        # Check required permissions exist
        allow = settings.get("permissions", {}).get("allow", [])
        for perm in self.REQUIRED_PERMISSIONS:
            if perm not in allow:
                return False

        return True

    def fix(self) -> CheckResult:
        if not self.SETTINGS.exists():
            return CheckResult(
                self.name, Status.SKIP,
                "settings.json does not exist — will be created by install"
            )

        actions = []
        text = self.SETTINGS.read_text()

        # Fix stale paths
        for old, new in self.STALE_PATTERNS.items():
            if old in text:
                text = text.replace(old, new)
                actions.append(f"replaced {old} → {new}")

        try:
            settings = json.loads(text)
        except json.JSONDecodeError:
            return CheckResult(
                self.name, Status.NOTIFY,
                "settings.json is malformed JSON — cannot safely repair",
                notify=True,
            )

        # Add missing required hooks
        hooks = settings.setdefault("hooks", {})
        for event, spec in self.REQUIRED_HOOKS.items():
            if not self._hook_exists(hooks, event, spec["command"]):
                if event not in hooks:
                    hooks[event] = []

                entry = {"type": "command", "command": spec["command"]}
                if spec.get("statusMessage"):
                    entry["statusMessage"] = spec["statusMessage"]
                if spec.get("async"):
                    entry["async"] = True

                hooks[event].append({"hooks": [entry]})
                actions.append(f"added {event} hook")

        settings["hooks"] = hooks

        # Add missing permissions
        perms = settings.setdefault("permissions", {})
        allow = perms.setdefault("allow", [])
        for perm in self.REQUIRED_PERMISSIONS:
            if perm not in allow:
                allow.append(perm)
                actions.append(f"added permission: {perm}")

        settings["permissions"]["allow"] = allow
        self.SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")

        if actions:
            return CheckResult(
                self.name, Status.FIXED,
                f"Fixed settings: {'; '.join(actions)}"
            )
        return CheckResult(self.name, Status.OK, "ok")

    def _hook_exists(self, hooks: dict, event: str, command: str) -> bool:
        """Check if a hook command is registered under an event."""
        event_hooks = hooks.get(event, [])
        if not isinstance(event_hooks, list):
            return False
        for h in event_hooks:
            if isinstance(h, dict):
                if h.get("command") == command:
                    return True
                # Nested: {"hooks": [{"command": "..."}]}
                for inner in h.get("hooks", []):
                    if isinstance(inner, dict) and inner.get("command") == command:
                        return True
        return False
