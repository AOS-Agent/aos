"""
Invariant: settings.json has correct hooks, paths, and permissions.

Checks:
1. Hook commands reference valid ~/aos/ paths (not stale prefixes)
2. Required hooks are registered
3. Permissions use blanket tool-level allows (Bash, Read, Edit, Write)

Note on permissions: Claude Code 2.1.78+ protects .claude/, .git/, .vscode/,
and .idea/ directories even in bypassPermissions mode. Path-specific allows
like "Edit ~/.claude/**" no longer override this. Blanket tool allows ("Edit",
"Write") DO override it. We enforce blanket allows and clean up old-format
path-specific rules left over from earlier versions.
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

    # Required permissions — blanket tool-level allows.
    # Claude Code 2.1.78+ protects .claude/ even in bypassPermissions mode.
    # Path-specific rules ("Edit ~/.claude/**") no longer work. Blanket
    # tool allows ("Edit") override the protection correctly.
    REQUIRED_PERMISSIONS = [
        "Bash",
        "Read",
        "Edit",
        "Write",
    ]

    # Old-format permissions to remove (superseded by blanket allows)
    STALE_PERMISSIONS = [
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

        # Check for stale permissions that should be removed
        for perm in self.STALE_PERMISSIONS:
            if perm in allow:
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

        # Remove stale old-format permissions
        perms = settings.setdefault("permissions", {})
        allow = perms.setdefault("allow", [])
        for perm in self.STALE_PERMISSIONS:
            if perm in allow:
                allow.remove(perm)
                actions.append(f"removed stale permission: {perm}")

        # Add missing blanket permissions
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
