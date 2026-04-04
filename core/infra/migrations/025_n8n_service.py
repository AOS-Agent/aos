"""
Migration 025: Install n8n automation engine as an AOS service.

Sets up n8n to run headlessly on localhost:5678, managed by a LaunchAgent.
n8n provides the workflow execution engine for Qareen automations —
400+ integrations, webhooks, cron scheduling, retries, and execution history.

Steps:
1. Create data directory at ~/.aos/services/n8n/
2. Install n8n globally via npm (if not present)
3. Generate API key, store in macOS Keychain
4. Deploy LaunchAgent plist from template
5. Bootstrap and start the service
"""

DESCRIPTION = "Install n8n automation engine as a managed AOS service"

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

HOME = Path.home()
N8N_DATA_DIR = HOME / ".aos" / "services" / "n8n"
N8N_CONFIG_DIR = N8N_DATA_DIR / ".n8n"
LOG_DIR = HOME / ".aos" / "logs"
PLIST_NAME = "com.aos.n8n"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
TEMPLATE_PATH = HOME / "aos" / "config" / "launchagents" / f"{PLIST_NAME}.plist.template"
AGENT_SECRET = HOME / "aos" / "core" / "bin" / "cli" / "agent-secret"


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _has_n8n() -> bool:
    """Check if n8n binary is available."""
    return _run(["which", "n8n"], timeout=5).returncode == 0


def _has_api_key() -> bool:
    """Check if N8N_API_KEY exists in Keychain."""
    result = _run([str(AGENT_SECRET), "get", "N8N_API_KEY"], timeout=5)
    return result.returncode == 0 and result.stdout.strip() != ""


def _is_healthy() -> bool:
    """Check if n8n is responding on port 5678."""
    try:
        from urllib.request import urlopen
        with urlopen("http://127.0.0.1:5678/healthz", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def check() -> bool:
    """Applied if n8n data dir exists, binary available, plist deployed."""
    if not N8N_DATA_DIR.exists():
        return False
    if not _has_n8n():
        return False
    if not PLIST_PATH.exists():
        return False
    if not _has_api_key():
        return False
    return True


def up() -> bool:
    """Install and configure n8n."""

    # 1. Create data directory
    N8N_DATA_DIR.mkdir(parents=True, exist_ok=True)
    N8N_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Created {N8N_DATA_DIR}")

    # 2. Install n8n via npm if not present
    if not _has_n8n():
        print("  Installing n8n via npm (this may take a minute)...")
        result = _run(["npm", "install", "-g", "n8n"], timeout=300)
        if result.returncode != 0:
            print(f"  ERROR: npm install failed: {result.stderr}")
            return False
        print("  n8n installed successfully")
    else:
        version = _run(["n8n", "--version"], timeout=10)
        print(f"  n8n already installed: v{version.stdout.strip()}")

    # 3. Generate and store API key
    if not _has_api_key():
        api_key = secrets.token_urlsafe(32)
        result = _run([str(AGENT_SECRET), "set", "N8N_API_KEY", api_key])
        if result.returncode != 0:
            print(f"  ERROR: Failed to store API key: {result.stderr}")
            return False
        print("  API key generated and stored in Keychain")
    else:
        print("  API key already exists in Keychain")

    # 4. n8n auto-generates its own config on first start (with encryption key).
    #    Do NOT write config before first start — n8n manages this file.
    print("  n8n will auto-generate config on first start")

    # 5. Deploy LaunchAgent from template
    if not TEMPLATE_PATH.exists():
        print(f"  ERROR: Plist template not found at {TEMPLATE_PATH}")
        return False

    template = TEMPLATE_PATH.read_text()
    plist_content = template.replace("__HOME__", str(HOME))
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    print(f"  Deployed plist to {PLIST_PATH}")

    # 6. Bootstrap and start the service
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"gui/{uid}/{PLIST_NAME}"

    # Bootout first (ignore failure)
    _run(["launchctl", "bootout", service], timeout=10)
    time.sleep(1)

    # Bootstrap
    result = _run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], timeout=10)
    if result.returncode != 0:
        print(f"  WARNING: bootstrap returned {result.returncode}: {result.stderr}")

    # Kickstart
    _run(["launchctl", "kickstart", "-k", service], timeout=10)
    print("  LaunchAgent started")

    # 7. Wait for health (up to 30s — n8n takes a moment to start)
    print("  Waiting for n8n to become healthy...")
    for i in range(15):
        time.sleep(2)
        if _is_healthy():
            print(f"  n8n healthy after {(i + 1) * 2}s")
            return True

    print("  WARNING: n8n not healthy after 30s — check ~/.aos/logs/n8n.err.log")
    # Return True anyway — the service may still be starting up.
    # The reconcile check will handle ongoing health monitoring.
    return True
