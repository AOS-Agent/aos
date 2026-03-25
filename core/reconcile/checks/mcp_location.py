"""
Invariant: mcp.json lives at ~/.claude/mcp.json (where Claude Code reads it).

Historical issue: early installs put it in ~/aos/config/mcp.json or
~/.aos/config/mcp.json. Claude Code never reads those locations.

MCP server registration architecture:
  - User-scope servers → ~/.claude.json (via `claude mcp add --scope user`)
  - Project-scope servers → .mcp.json in project root
  - ~/.claude/mcp.json acts as a project-level config for sessions in ~/.claude/
  - settings.json mcpServers is for approval policies, NOT server definitions

Note: deduplication between mcp.json and settings.json is legacy.
Settings.json mcpServers is no longer used for server definitions.
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class McpLocationCheck(ReconcileCheck):
    name = "mcp_json_location"
    description = "mcp.json is at ~/.claude/mcp.json"

    RIGHT = Path.home() / ".claude" / "mcp.json"
    SETTINGS = Path.home() / ".claude" / "settings.json"
    WRONG_LOCATIONS = [
        Path.home() / "aos" / "config" / "mcp.json",
        Path.home() / ".aos" / "config" / "mcp.json",
    ]

    def _settings_servers(self) -> set:
        """Get server names already defined in settings.json mcpServers."""
        if not self.SETTINGS.exists():
            return set()
        try:
            data = json.loads(self.SETTINGS.read_text())
            return set(data.get("mcpServers", {}).keys())
        except (json.JSONDecodeError, OSError):
            return set()

    def check(self) -> bool:
        # No stale copies should exist
        if any(p.exists() for p in self.WRONG_LOCATIONS):
            return False

        # No duplicates between mcp.json and settings.json
        if self.RIGHT.exists():
            try:
                mcp_data = json.loads(self.RIGHT.read_text())
                mcp_servers = set(mcp_data.get("mcpServers", {}).keys())
                settings_servers = self._settings_servers()
                if mcp_servers & settings_servers:
                    return False  # Duplicates found
            except (json.JSONDecodeError, OSError):
                pass

        return True

    def fix(self) -> CheckResult:
        actions = []

        for wrong in self.WRONG_LOCATIONS:
            if not wrong.exists():
                continue

            try:
                wrong_data = json.loads(wrong.read_text())
            except (json.JSONDecodeError, OSError):
                wrong.unlink()
                actions.append(f"removed corrupt {wrong}")
                continue

            if self.RIGHT.exists():
                # Merge: right is authoritative, wrong fills gaps
                try:
                    right_data = json.loads(self.RIGHT.read_text())
                except (json.JSONDecodeError, OSError):
                    right_data = {}

                wrong_servers = wrong_data.get("mcpServers", {})
                right_servers = right_data.get("mcpServers", {})

                # Add servers from wrong location that don't exist in right
                # AND aren't already in settings.json (avoid duplicates)
                settings_servers = self._settings_servers()
                for name, spec in wrong_servers.items():
                    if name not in right_servers and name not in settings_servers:
                        right_servers[name] = spec

                right_data["mcpServers"] = right_servers
                self.RIGHT.write_text(json.dumps(right_data, indent=2) + "\n")
                wrong.unlink()
                actions.append(f"merged {wrong} into {self.RIGHT}")
            else:
                # Only wrong location exists — move it
                self.RIGHT.parent.mkdir(parents=True, exist_ok=True)
                wrong.rename(self.RIGHT)
                actions.append(f"moved {wrong} to {self.RIGHT}")

        # Deduplicate: remove servers from mcp.json that are already in settings.json
        if self.RIGHT.exists():
            try:
                right_data = json.loads(self.RIGHT.read_text())
                right_servers = right_data.get("mcpServers", {})
                settings_servers = self._settings_servers()
                dupes = set(right_servers.keys()) & settings_servers
                if dupes:
                    for name in dupes:
                        del right_servers[name]
                    right_data["mcpServers"] = right_servers
                    self.RIGHT.write_text(json.dumps(right_data, indent=2) + "\n")
                    actions.append(f"removed duplicates from mcp.json: {', '.join(dupes)}")
            except (json.JSONDecodeError, OSError):
                pass

        if actions:
            return CheckResult(
                self.name, Status.FIXED,
                f"Fixed mcp.json: {'; '.join(actions)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
