"""
Migration 059: Replace workspace-mcp MCP server with gws CLI.

(Renumbered from 043 during release-train wave 3 promotion; see 056's
docstring for why the renumber is load-bearing, not cosmetic. Also
converted to return True/False — see up()'s docstring — and to write
~/.claude.json atomically, since that file is actively read/written
by any running Claude Code session — see up()'s docstring.)

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
import os
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


def _atomic_write_json(path: Path, data: dict):
    """Write JSON atomically: backup + temp-file-then-rename.

    ~/.claude.json is actively read and written by any running Claude
    Code session (including via /config). A plain read-modify-write
    here risks a torn write racing a concurrent session. Take a .bak
    copy first, then write to a sibling temp file and os.replace() it
    into place — os.replace() is atomic on the same filesystem, so
    readers never observe a partially-written file.
    """
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def up() -> bool:
    """Convert credentials, deregister legacy MCP, check for gws CLI.

    True on success (including "nothing to do"), False only on a
    genuine failure (a credential file that couldn't be converted, or
    ~/.claude.json couldn't be cleaned) — never a string, per
    runner.py's strict True/None-only success contract. A missing gws
    binary is informational only (this migration only detects and
    warns; it doesn't install), so it does not fail the migration.
    """
    results = []
    failed = False

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
                failed = True

    # 2. Remove legacy MCP server registrations
    try:
        data = json.loads(CLAUDE_JSON.read_text()) if CLAUDE_JSON.exists() else {}
        servers = data.get("mcpServers", {})
        removed = []
        for name in LEGACY_MCP_NAMES:
            if name in servers:
                del servers[name]
                removed.append(name)
        if removed:
            _atomic_write_json(CLAUDE_JSON, data)
            results.append(f"Removed MCP servers: {', '.join(removed)}")
    except Exception as e:
        results.append(f"Failed to clean MCP registrations: {e}")
        failed = True

    # 3. Check gws CLI is installed (detect + warn only; never fails the migration)
    try:
        r = subprocess.run(["which", "gws"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            results.append(f"gws CLI found at {r.stdout.strip()}")
        else:
            results.append("WARNING: gws CLI not installed — run: brew install googleworkspace-cli")
    except Exception:
        results.append("WARNING: could not check for gws CLI")

    print("; ".join(results) if results else "No changes needed")
    return not failed
