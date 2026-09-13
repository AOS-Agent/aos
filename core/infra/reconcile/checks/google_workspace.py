"""
Invariant: Google Workspace access via gws CLI is correctly configured.

Checks:
1. gws binary is installed (via Homebrew)
2. gws-account wrapper exists and is executable
3. OAuth credentials exist in macOS Keychain
4. At least one account credential file exists
5. Legacy workspace-mcp MCP server is not registered
6. Every account credential file is mode 0600 (aos#2326 — the refresh token
   inside is plaintext by necessity, since gws itself reads the file
   directly; NOTIFY, never auto-chmod, on drift)
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

    def _legacy_mcp_registered(self) -> list[str]:
        """Return list of legacy MCP server names still registered."""
        try:
            data = json.loads(self.CLAUDE_JSON.read_text())
        except Exception:
            return []
        servers = data.get("mcpServers", {})
        return [n for n in self.LEGACY_MCP_NAMES if n in servers]

    def _insecure_permission_files(self) -> list[Path]:
        """Credential files whose mode is looser than 0600.

        aos#2326: these hold a live Google OAuth refresh token in plaintext.
        `gws` itself owns this file format (invoked with
        GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE by gws-account) so it can't be
        moved into Keychain without breaking gws — but it must never be
        readable by anyone but the operator. A `gws` token refresh can
        rewrite the file under the process umask, so this is checked on
        every reconcile cycle, not just at migration time.
        """
        if not self.CREDS_DIR.is_dir():
            return []
        insecure = []
        for f in sorted(self.CREDS_DIR.glob("*.json")):
            try:
                if (f.stat().st_mode & 0o777) != 0o600:
                    insecure.append(f)
            except OSError:
                continue
        return insecure

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
        if self._insecure_permission_files():
            return False
        return True

    def fix(self) -> CheckResult:
        issues = []

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

        # aos#2326: never silently accept a world/group-readable credential
        # file — NOTIFY, don't auto-chmod behind the operator's back. (The
        # one-time hardening on write lives in migration 134 / migration 059.)
        insecure = self._insecure_permission_files()
        if insecure:
            names = ", ".join(f.name for f in insecure)
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=(
                    f"Google credential file(s) not 0600 (readable beyond the "
                    f"operator): {names} — run: chmod 600 <file>"
                ),
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
