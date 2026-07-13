"""
Migration 056: Install n8n automation engine as an AOS service.

(Renumbered from 025 during release-train wave 3 promotion — 025 was the
council-substrate dev number; 056 is main's next free slot after wave 2's
053-055. See core/infra/migrations/runner.py: progress is a single integer
high-watermark, so a lower number here would be silently skipped forever
on any instance already past 055.)

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
import socket
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


def _has_npm() -> bool:
    """Check npm is on PATH before attempting an install.

    Without this, a missing npm surfaces as a raw FileNotFoundError from
    deep inside `npm install -g n8n`, caught only by the migration
    runner's generic exception handler. Checking first lets us fail with
    a clear, actionable message instead of a stderr blob.
    """
    try:
        return _run(["npm", "--version"], timeout=10).returncode == 0
    except FileNotFoundError:
        return False


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if something is already listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


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
    """Applied if n8n data dir exists, binary available, plist deployed, and healthy.

    The health check aligns this with core/infra/reconcile/checks/n8n.py's
    check(), which is the real ongoing-health backstop (it re-kickstarts
    on drift after the migration has run once). Adding it here too is
    consistency, not a substitute — the migration only ever runs once,
    gated by the version watermark, so it can't replace the reconcile
    check's repeated monitoring.
    """
    if not N8N_DATA_DIR.exists():
        return False
    if not _has_n8n():
        return False
    if not PLIST_PATH.exists():
        return False
    if not _has_api_key():
        return False
    if not _is_healthy():
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
        if not _has_npm():
            print("  ERROR: npm not found on PATH. n8n requires Node.js/npm to install")
            print("  (e.g. `brew install node`) — install that first, then retry.")
            return False
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

    # 5.5 Port-conflict pre-check. n8n binds :5678; something else already
    # listening there (not our own about-to-be-restarted n8n) means the
    # bootstrap below will bind-fail, /healthz will never respond, the
    # 30s wait times out, and up() would otherwise return True anyway —
    # a silent, permanent unhealthy state (the reconcile check would keep
    # re-kickstarting a service that can never come up). Distinguish that
    # from "still starting" and fail loudly here instead.
    if _port_open(5678) and not _is_healthy():
        print("  ERROR: port 5678 is already in use by something other than n8n.")
        print("  Free the port (or reconfigure n8n's port) before retrying —")
        print("  n8n cannot bind and would otherwise time out silently.")
        return False

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

    if _port_open(5678):
        print("  WARNING: n8n not healthy after 30s, but port 5678 is bound —")
        print("  likely still starting. Check ~/.aos/logs/n8n.err.log.")
    else:
        print("  WARNING: n8n not healthy after 30s and port 5678 is not bound —")
        print("  the process likely failed to start. Check ~/.aos/logs/n8n.err.log.")
    # Return True anyway in both cases — the reconcile check
    # (core/infra/reconcile/checks/n8n.py) owns ongoing health monitoring
    # and will keep retrying/notifying past this point.
    return True
