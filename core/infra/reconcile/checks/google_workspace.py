"""
Invariant: Google Workspace access via gws CLI is correctly configured.

Checks:
1. gws binary is installed (via Homebrew)
2. gws-account wrapper exists and is executable
3. OAuth credentials exist in macOS Keychain
4. At least one account credential file exists
5. Legacy workspace-mcp MCP server is not registered
6. client_secret.json has project_id stripped (multi-account fix)
   gws sends x-goog-user-project header from client_secret.json's project_id,
   which triggers 403s for accounts that aren't IAM members of the GCP project.
   Stripping project_id prevents the header from being sent. The full copy is
   preserved at ~/.aos/config/google/client_secret.full.json for gws auth login.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


class GoogleWorkspaceCheck(ReconcileCheck):
    name = "google_workspace"
    description = "Google Workspace gws CLI is configured and healthy"

    AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"
    GWS_ACCOUNT = Path.home() / "aos" / "core" / "bin" / "internal" / "gws-account"
    CREDS_DIR = Path.home() / ".aos" / "config" / "google" / "credentials"
    GWS_CONFIG_DIR = Path.home() / ".config" / "gws"
    CLIENT_SECRET = GWS_CONFIG_DIR / "client_secret.json"
    CLIENT_SECRET_FULL = Path.home() / ".aos" / "config" / "google" / "client_secret.full.json"
    CLAUDE_JSON = Path.home() / ".claude.json"
    REQUIRED_SECRETS = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_PRIMARY_EMAIL",
    ]
    LEGACY_MCP_NAMES = ["google-workspace", "mcp-gsuite", "mcp_gsuite", "gsuite"]

    def _has_secret(self, name: str) -> bool:
        try:
            result = subprocess.run(
                [str(self.AGENT_SECRET), "get", name],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def _client_secret_has_project_id(self) -> bool:
        """Return True if client_secret.json still has project_id (needs stripping)."""
        try:
            data = json.loads(self.CLIENT_SECRET.read_text())
            key = "installed" if "installed" in data else "web"
            return "project_id" in data.get(key, {})
        except Exception:
            return False

    def _strip_project_id(self) -> str | None:
        """Strip project_id from client_secret.json, saving full copy first."""
        try:
            raw = self.CLIENT_SECRET.read_text()
            data = json.loads(raw)
            key = "installed" if "installed" in data else "web"
            if "project_id" not in data.get(key, {}):
                return None

            # Save full copy (with project_id) for gws auth login
            self.CLIENT_SECRET_FULL.parent.mkdir(parents=True, exist_ok=True)
            if not self.CLIENT_SECRET_FULL.exists():
                self.CLIENT_SECRET_FULL.write_text(raw)

            # Strip project_id from runtime copy
            del data[key]["project_id"]
            self.CLIENT_SECRET.write_text(json.dumps(data, indent=2) + "\n")
            return "Stripped project_id from client_secret.json (multi-account fix)"
        except Exception as e:
            return f"Failed to strip project_id: {e}"

    def _legacy_mcp_registered(self) -> list[str]:
        """Return list of legacy MCP server names still registered."""
        try:
            data = json.loads(self.CLAUDE_JSON.read_text())
        except Exception:
            return []
        servers = data.get("mcpServers", {})
        return [n for n in self.LEGACY_MCP_NAMES if n in servers]

    def check(self) -> bool:
        if not shutil.which("gws"):
            return False
        if not self.GWS_ACCOUNT.exists():
            return False
        if not all(self._has_secret(s) for s in self.REQUIRED_SECRETS):
            return False
        if not self.CREDS_DIR.is_dir() or not any(self.CREDS_DIR.glob("*.json")):
            return False
        if self._legacy_mcp_registered():
            return False
        if self._client_secret_has_project_id():
            return False
        return True

    def fix(self) -> CheckResult:
        issues = []

        # Strip project_id from client_secret.json (multi-account fix)
        if self._client_secret_has_project_id():
            result = self._strip_project_id()
            if result:
                issues.append(result)

        # Remove legacy MCP registrations
        legacy = self._legacy_mcp_registered()
        if legacy:
            try:
                data = json.loads(self.CLAUDE_JSON.read_text())
                for name in legacy:
                    data.get("mcpServers", {}).pop(name, None)
                self.CLAUDE_JSON.write_text(json.dumps(data, indent=2) + "\n")
                issues.append(f"Removed legacy MCP servers: {', '.join(legacy)}")
            except Exception as e:
                issues.append(f"Failed to remove legacy MCP: {e}")

        # Check gws binary
        if not shutil.which("gws"):
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="gws CLI not installed — run: brew install googleworkspace-cli",
                notify=True,
            )

        # Check wrapper
        if not self.GWS_ACCOUNT.exists():
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="gws-account wrapper missing at core/bin/internal/gws-account",
                notify=True,
            )

        # Check secrets
        missing = [s for s in self.REQUIRED_SECRETS if not self._has_secret(s)]
        if missing:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=f"Missing Keychain secrets: {', '.join(missing)}",
                notify=True,
            )

        # Check credential files
        if not self.CREDS_DIR.is_dir() or not any(self.CREDS_DIR.glob("*.json")):
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="No Google credential files in ~/.aos/config/google/credentials/",
                notify=True,
            )

        if issues:
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message="Google Workspace repaired",
                detail="; ".join(issues),
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            message="Google Workspace gws CLI is correctly configured",
        )
