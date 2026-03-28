"""
Invariant: Google Workspace MCP server is correctly configured.

Checks:
1. workspace-mcp binary is installed (via uv tool)
2. Wrapper script exists at core/bin/google-workspace-mcp and uses absolute path
3. OAuth credentials exist in macOS Keychain
4. MCP server is registered at user scope via `claude mcp add`
   (NOT in settings.json mcpServers — Claude Code ignores that location)

MCP server registration:
  Claude Code reads MCP servers from ~/.claude.json (user scope) or
  .mcp.json (project scope). The settings.json mcpServers key is for
  approval policies only, not server definitions.
"""

import json
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class GoogleWorkspaceCheck(ReconcileCheck):
    name = "google_workspace"
    description = "Google Workspace MCP server is configured and healthy"

    CLAUDE_JSON = Path.home() / ".claude.json"
    WRAPPER = Path.home() / "aos" / "core" / "bin" / "google-workspace-mcp"
    AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"
    REQUIRED_SECRETS = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_PRIMARY_EMAIL",
    ]

    def _read_claude_json(self):
        """Read ~/.claude.json safely."""
        try:
            return json.loads(self.CLAUDE_JSON.read_text())
        except Exception:
            return {}

    def _mcp_registered(self) -> bool:
        """Check if google-workspace is registered in ~/.claude.json."""
        data = self._read_claude_json()
        servers = data.get("mcpServers", {})
        if "google-workspace" not in servers:
            return False
        entry = servers["google-workspace"]
        command = entry.get("command", "")
        # Accept either wrapper path or the wrapper as the command
        return str(self.WRAPPER) in command or "google-workspace-mcp" in command

    def check(self) -> bool:
        # 1. Wrapper script exists and is executable
        if not self.WRAPPER.exists() or not self.WRAPPER.stat().st_mode & 0o111:
            return False

        # 2. Wrapper uses absolute path for workspace-mcp binary
        try:
            content = self.WRAPPER.read_text()
            if "exec workspace-mcp" in content and "$HOME/.local/bin/workspace-mcp" not in content:
                return False
        except Exception:
            return False

        # 3. workspace-mcp binary exists
        workspace_mcp = Path.home() / ".local" / "bin" / "workspace-mcp"
        if not workspace_mcp.exists():
            try:
                result = subprocess.run(
                    ["which", "workspace-mcp"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    return False
            except Exception:
                return False

        # 4. Keychain secrets exist
        for secret in self.REQUIRED_SECRETS:
            try:
                result = subprocess.run(
                    [str(self.AGENT_SECRET), "get", secret],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0 or not result.stdout.strip():
                    return False
            except Exception:
                return False

        # 5. MCP server registered at user scope in ~/.claude.json
        if not self._mcp_registered():
            return False

        return True

    def fix(self) -> CheckResult:
        issues = []

        # Check wrapper
        if not self.WRAPPER.exists():
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="Wrapper script missing at core/bin/google-workspace-mcp",
                notify=True,
            )

        # Check binary
        workspace_mcp = Path.home() / ".local" / "bin" / "workspace-mcp"
        if not workspace_mcp.exists():
            try:
                result = subprocess.run(
                    ["uv", "tool", "install", "workspace-mcp"],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    issues.append("Reinstalled workspace-mcp via uv tool")
                else:
                    return CheckResult(
                        name=self.name,
                        status=Status.NOTIFY,
                        message="workspace-mcp binary missing and reinstall failed",
                        detail=result.stderr,
                        notify=True,
                    )
            except Exception as e:
                return CheckResult(
                    name=self.name,
                    status=Status.ERROR,
                    message=f"Failed checking workspace-mcp binary: {e}",
                )

        # Check secrets
        missing_secrets = []
        for secret in self.REQUIRED_SECRETS:
            try:
                result = subprocess.run(
                    [str(self.AGENT_SECRET), "get", secret],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0 or not result.stdout.strip():
                    missing_secrets.append(secret)
            except Exception:
                missing_secrets.append(secret)

        if missing_secrets:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=f"Missing Keychain secrets: {', '.join(missing_secrets)}",
                notify=True,
            )

        # Fix MCP registration — use claude mcp add (the correct approach)
        if not self._mcp_registered():
            try:
                result = subprocess.run(
                    [
                        "claude", "mcp", "add",
                        "--scope", "user",
                        "--transport", "stdio",
                        "google-workspace",
                        str(self.WRAPPER),
                    ],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    issues.append("Registered google-workspace MCP at user scope via claude mcp add")
                else:
                    return CheckResult(
                        name=self.name,
                        status=Status.ERROR,
                        message="Failed to register MCP server via claude mcp add",
                        detail=result.stderr,
                    )
            except Exception as e:
                return CheckResult(
                    name=self.name,
                    status=Status.ERROR,
                    message=f"Failed registering MCP server: {e}",
                )

        if issues:
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message="Google Workspace MCP repaired",
                detail="; ".join(issues),
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            message="Google Workspace MCP is correctly configured",
        )
