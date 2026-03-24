"""
Invariant: Google Workspace MCP server is correctly configured.

Checks:
1. workspace-mcp binary is installed (via uv tool)
2. Wrapper script exists at core/bin/google-workspace-mcp
3. OAuth credentials exist in macOS Keychain
4. settings.json has the google-workspace MCP server entry
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

    SETTINGS = Path.home() / ".claude" / "settings.json"
    WRAPPER = Path.home() / "aos" / "core" / "bin" / "google-workspace-mcp"
    AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"
    REQUIRED_SECRETS = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_PRIMARY_EMAIL",
    ]

    def check(self) -> bool:
        # 1. Wrapper script exists and is executable
        if not self.WRAPPER.exists() or not self.WRAPPER.stat().st_mode & 0o111:
            return False

        # 2. workspace-mcp binary exists
        try:
            result = subprocess.run(
                ["which", "workspace-mcp"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return False
        except Exception:
            return False

        # 3. Keychain secrets exist
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

        # 4. settings.json has the MCP server entry
        try:
            settings = json.loads(self.SETTINGS.read_text())
            servers = settings.get("mcpServers", {})
            if "google-workspace" not in servers:
                return False
            entry = servers["google-workspace"]
            if entry.get("command") != str(self.WRAPPER):
                return False
        except Exception:
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
        try:
            result = subprocess.run(
                ["which", "workspace-mcp"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                # Try to reinstall
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

        # Fix settings.json entry
        try:
            settings = json.loads(self.SETTINGS.read_text())
            servers = settings.setdefault("mcpServers", {})
            if "google-workspace" not in servers or servers["google-workspace"].get("command") != str(self.WRAPPER):
                servers["google-workspace"] = {
                    "command": str(self.WRAPPER),
                    "args": [],
                    "env": {},
                }
                self.SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
                issues.append("Added google-workspace to settings.json mcpServers")
        except Exception as e:
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Failed updating settings.json: {e}",
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
