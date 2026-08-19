"""
Migration 109: retire the memory MCP server (operator-approved 2026-08-18).

The ChromaDB-backed "memory" MCP server never earned its keep: on the
reference machine its index directory was 0 bytes — not one document ever
indexed — no skill referenced its tools, and QMD (thousands of docs across
its collections) is the production search/memory layer. Code removed from
core/services/memory in the same commit; sync-mcp no longer registers it.

This migration cleans each machine:
  1. Removes the "memory" entry from mcpServers in ~/.claude.json (the
     user-scope registry sync-mcp writes) and legacy ~/.claude/mcp.json.
  2. Deletes ~/.aos/services/memory (venv, ~300 MB) and ~/.aos/data/memory
     (the empty index).
  3. Removes a com.aos.memory LaunchAgent if some ancient install left one.
"""

DESCRIPTION = "Retire memory MCP: mcp registrations, venv, empty index"

import json
import os
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
VENV_DIR = HOME / ".aos" / "services" / "memory"
DATA_DIR = HOME / ".aos" / "data" / "memory"
MCP_FILES = [HOME / ".claude.json", HOME / ".claude" / "mcp.json"]
PLIST = HOME / "Library" / "LaunchAgents" / "com.aos.memory.plist"


def _deregister() -> list[str]:
    cleaned = []
    for f in MCP_FILES:
        if not f.exists():
            continue
        try:
            config = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        servers = config.get("mcpServers") or {}
        if "memory" not in servers:
            continue
        del servers["memory"]
        # Atomic write — ~/.claude.json is live-read by running sessions.
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(config, indent=2) + "\n")
        os.replace(tmp, f)
        cleaned.append(str(f))
    return cleaned


def check() -> bool:
    if VENV_DIR.exists() or DATA_DIR.exists():
        return False
    for f in MCP_FILES:
        if f.exists():
            try:
                if "memory" in (json.loads(f.read_text()).get("mcpServers") or {}):
                    return False
            except (json.JSONDecodeError, OSError):
                pass
    return True


def up() -> bool:
    cleaned = _deregister()
    removed = []
    for d in (VENV_DIR, DATA_DIR):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(str(d))
    if PLIST.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/com.aos.memory"],
            capture_output=True, timeout=30,
        )
        PLIST.unlink()
        removed.append(str(PLIST))
    print(f"  MCP registrations cleaned: {', '.join(cleaned) if cleaned else 'none'}")
    print(f"  Removed: {', '.join(removed) if removed else 'nothing on disk'}")
    return check()


def down() -> bool:
    return False
