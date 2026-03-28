"""
Invariant: ~/.claude.json has AOS always-on defaults.

Claude Code stores some preferences in ~/.claude.json (NOT settings.json).
These are runtime flags typically set via /config UI. AOS requires certain
features always enabled for all users.

Verified keys (see rules/claude-code-config.md):
  - remoteControlAtStartup: true  (Remote Control for all sessions)
  - claudeInChromeDefaultEnabled: true  (Chrome MCP for all sessions)

These keys are NOT in settings.json — they live in ~/.claude.json.
Don't assume key names; they were discovered by toggling /config and
diffing the file. If new keys are needed, verify the same way.
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


# Keys that must be true in ~/.claude.json for AOS
# Format: {key: expected_value}
REQUIRED_DEFAULTS = {
    "remoteControlAtStartup": True,
    "claudeInChromeDefaultEnabled": True,
}


class ClaudeDefaultsCheck(ReconcileCheck):
    name = "claude_defaults"
    description = "~/.claude.json has AOS always-on defaults (remote control, chrome)"

    target = Path.home() / ".claude.json"

    def check(self) -> bool:
        if not self.target.exists():
            return False
        try:
            data = json.loads(self.target.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        for key, expected in REQUIRED_DEFAULTS.items():
            if data.get(key) != expected:
                return False
        return True

    def fix(self) -> CheckResult:
        try:
            data = json.loads(self.target.read_text()) if self.target.exists() else {}
        except (json.JSONDecodeError, OSError):
            data = {}

        fixed = []
        for key, expected in REQUIRED_DEFAULTS.items():
            if data.get(key) != expected:
                data[key] = expected
                fixed.append(key)

        if fixed:
            self.target.write_text(json.dumps(data, indent=2) + "\n")
            return CheckResult(
                self.name, Status.FIXED,
                f"Set {', '.join(fixed)} in ~/.claude.json"
            )
        return CheckResult(self.name, Status.OK, "ok")
