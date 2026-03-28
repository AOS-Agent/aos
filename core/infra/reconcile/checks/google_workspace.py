"""
Invariant: Google Workspace MCP server is correctly configured.

Checks:
1. workspace-mcp binary is installed (via uv tool)
2. Wrapper script exists at core/bin/internal/google-workspace-mcp and uses absolute path
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
    CLAUDE_MCP_JSON = Path.home() / ".claude" / "mcp.json"
    WRAPPER = Path.home() / "aos" / "core" / "bin" / "internal" / "google-workspace-mcp"
    AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"
    REQUIRED_SECRETS = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_PRIMARY_EMAIL",
    ]
    # Legacy server names to remove during migration
    LEGACY_NAMES = ["mcp-gsuite", "mcp_gsuite", "gsuite"]

    def _read_json(self, path: Path) -> dict:
        """Read a JSON file safely."""
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _write_json(self, path: Path, data: dict):
        """Write a JSON file preserving formatting."""
        path.write_text(json.dumps(data, indent=2) + "\n")

    def _read_claude_json(self):
        return self._read_json(self.CLAUDE_JSON)

    def _legacy_registered(self) -> list[str]:
        """Return list of legacy MCP server names still registered."""
        found = []
        for path in (self.CLAUDE_JSON, self.CLAUDE_MCP_JSON):
            data = self._read_json(path)
            servers = data.get("mcpServers", {})
            for name in self.LEGACY_NAMES:
                if name in servers:
                    found.append(f"{name} in {path.name}")
        return found

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
        # 0. Legacy MCP servers must not be registered
        if self._legacy_registered():
            return False

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

        # Remove legacy mcp-gsuite registrations
        for path in (self.CLAUDE_JSON, self.CLAUDE_MCP_JSON):
            data = self._read_json(path)
            servers = data.get("mcpServers", {})
            removed = []
            for name in self.LEGACY_NAMES:
                if name in servers:
                    del servers[name]
                    removed.append(name)
            if removed:
                self._write_json(path, data)
                issues.append(f"Removed legacy MCP servers ({', '.join(removed)}) from {path.name}")

        # Check wrapper
        if not self.WRAPPER.exists():
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="Wrapper script missing at core/bin/internal/google-workspace-mcp",
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
