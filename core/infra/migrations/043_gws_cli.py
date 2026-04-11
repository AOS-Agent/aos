"""
Migration 043: Replace workspace-mcp MCP server with gws CLI.

Converts OAuth token files from workspace-mcp format
(~/.google_workspace_mcp/credentials/) to gws-compatible format
(~/.aos/config/google/credentials/), and deregisters the legacy
MCP server from Claude Code.

The workspace-mcp credential directory is left in place for one
update cycle as a rollback safety net.

Idempotent: re-running is safe.
"""

DESCRIPTION = "Migrate Google Workspace from workspace-mcp to gws CLI"

import json
import subprocess
from pathlib import Path

OLD_CREDS_DIR = Path.home() / ".google_workspace_mcp" / "credentials"
NEW_CREDS_DIR = Path.home() / ".aos" / "config" / "google" / "credentials"
CLAUDE_JSON = Path.home() / ".claude.json"
LEGACY_MCP_NAMES = ["google-workspace", "mcp-gsuite", "mcp_gsuite", "gsuite"]


def check() -> bool:
    """Return True if migration has already been applied."""
    # Migration is done if new creds dir has files AND no legacy MCP registered
    if not NEW_CREDS_DIR.is_dir() or not any(NEW_CREDS_DIR.glob("*.json")):
        # No new creds yet — only skip if old creds also don't exist
        if not OLD_CREDS_DIR.is_dir() or not any(OLD_CREDS_DIR.glob("*.json")):
            return True  # Nothing to migrate
        return False

    # Check legacy MCP not registered
    try:
        data = json.loads(CLAUDE_JSON.read_text())
        servers = data.get("mcpServers", {})
        if any(n in servers for n in LEGACY_MCP_NAMES):
            return False
    except Exception:
        pass

    return True


def run() -> str:
    results = []

    # 1. Convert credential files
    NEW_CREDS_DIR.mkdir(parents=True, exist_ok=True)

    if OLD_CREDS_DIR.is_dir():
        for token_file in sorted(OLD_CREDS_DIR.glob("*.json")):
            dest = NEW_CREDS_DIR / token_file.name
            if dest.exists():
                results.append(f"Skipped {token_file.stem} (already converted)")
                continue

            try:
                data = json.loads(token_file.read_text())
                gws_cred = {
                    "client_id": data["client_id"],
                    "client_secret": data["client_secret"],
                    "refresh_token": data["refresh_token"],
                    "type": "authorized_user",
                }
                dest.write_text(json.dumps(gws_cred, indent=2) + "\n")
                results.append(f"Converted {token_file.stem}")
            except (KeyError, json.JSONDecodeError) as e:
                results.append(f"Failed to convert {token_file.stem}: {e}")

    # 2. Remove legacy MCP server registrations
    try:
        data = json.loads(CLAUDE_JSON.read_text())
        servers = data.get("mcpServers", {})
        removed = []
        for name in LEGACY_MCP_NAMES:
            if name in servers:
                del servers[name]
                removed.append(name)
        if removed:
            CLAUDE_JSON.write_text(json.dumps(data, indent=2) + "\n")
            results.append(f"Removed MCP servers: {', '.join(removed)}")
    except Exception as e:
        results.append(f"Failed to clean MCP registrations: {e}")

    # 3. Check gws CLI is installed
    try:
        r = subprocess.run(["which", "gws"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            results.append(f"gws CLI found at {r.stdout.strip()}")
        else:
            results.append("WARNING: gws CLI not installed — run: brew install googleworkspace-cli")
    except Exception:
        results.append("WARNING: could not check for gws CLI")

    return "; ".join(results) if results else "No changes needed"
