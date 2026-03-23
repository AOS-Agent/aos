"""
Invariant: mcp.json lives at ~/.claude/mcp.json (where Claude Code reads it).

Historical issue: early installs put it in ~/aos/config/mcp.json or
~/.aos/config/mcp.json. Claude Code never reads those locations.
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
    WRONG_LOCATIONS = [
        Path.home() / "aos" / "config" / "mcp.json",
        Path.home() / ".aos" / "config" / "mcp.json",
    ]

    def check(self) -> bool:
        # No stale copies should exist
        return not any(p.exists() for p in self.WRONG_LOCATIONS)

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
                for name, spec in wrong_servers.items():
                    if name not in right_servers:
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

        return CheckResult(
            self.name, Status.FIXED,
            f"Fixed mcp.json location: {'; '.join(actions)}"
        )
