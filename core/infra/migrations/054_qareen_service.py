"""
Migration 054: Deploy Qareen as the primary AOS service on port 4096.

Qareen is the unified intelligence core — FastAPI backend + React frontend.
It replaces the old dashboard service (which only served an HTML template).

Steps:
1. Create venv at ~/.aos/services/qareen/.venv/
2. Install Python dependencies from requirements.txt
3. Initialize database schemas (qareen.sql)
4. Ensure models directory exists for Silero VAD
5. Stop old dashboard service
6. Deploy LaunchAgent plist from template
7. Start Qareen on port 4096
8. Wait for health check

Numbering note: this shipped as migration 026 on the unshipped
council-substrate branch (see core/infra/migrations/050_work_db_ownership.py
for the full renumbering rationale). Renumbered to 054 to land after main's
001-052 (and after 053, the Qareen Tasks data model upgrade it depends on).
"""

DESCRIPTION = "Deploy Qareen service (replaces dashboard on port 4096)"

import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
QAREEN_DIR = AOS_ROOT / "core" / "qareen"
QAREEN_VENV = HOME / ".aos" / "services" / "qareen" / ".venv"
QAREEN_PYTHON = QAREEN_VENV / "bin" / "python"
REQUIREMENTS = QAREEN_DIR / "requirements.txt"
SCHEMA_SQL = QAREEN_DIR / "schemas" / "qareen.sql"
DB_PATH = HOME / ".aos" / "data" / "qareen.db"
MODELS_DIR = HOME / ".aos" / "models"
LOG_DIR = HOME / ".aos" / "logs"

PLIST_NAME = "com.aos.qareen"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
TEMPLATE_PATH = AOS_ROOT / "config" / "launchagents" / f"{PLIST_NAME}.plist.template"

OLD_DASHBOARD_PLIST = "com.aos.dashboard"
OLD_DASHBOARD_PATH = HOME / "Library" / "LaunchAgents" / f"{OLD_DASHBOARD_PLIST}.plist"

HEALTH_URL = "http://127.0.0.1:4096/api/health"


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if something is already listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _is_healthy() -> bool:
    """Check if Qareen health endpoint responds on port 4096."""
    try:
        from urllib.request import urlopen
        with urlopen(HEALTH_URL, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def check() -> bool:
    """Applied if qareen venv exists, plist is deployed, and health endpoint responds."""
    if not QAREEN_VENV.exists():
        return False
    if not PLIST_PATH.exists():
        return False
    if not _is_healthy():
        return False
    return True


def up() -> bool:
    """Deploy Qareen as the primary AOS service."""

    # 1. Create venv
    QAREEN_VENV.parent.mkdir(parents=True, exist_ok=True)
    if not QAREEN_PYTHON.exists():
        print("  Creating Qareen venv...")
        result = _run(["python3", "-m", "venv", str(QAREEN_VENV)])
        if result.returncode != 0:
            print(f"  ERROR: venv creation failed: {result.stderr}")
            return False
        print(f"  Created venv at {QAREEN_VENV}")
    else:
        print(f"  Venv already exists at {QAREEN_VENV}")

    # 2. Install dependencies
    print("  Installing Qareen dependencies...")
    if not REQUIREMENTS.exists():
        print(f"  ERROR: requirements.txt not found at {REQUIREMENTS}")
        return False

    result = _run(
        [str(QAREEN_PYTHON), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  ERROR: pip install failed: {result.stderr}")
        return False
    print("  Dependencies installed")

    # 3. Initialize database schemas
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SCHEMA_SQL.exists():
        print("  Initializing Qareen database schemas...")
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.executescript(SCHEMA_SQL.read_text())
            conn.close()
            print(f"  Database initialized at {DB_PATH}")
        except Exception as e:
            print(f"  WARNING: Schema init error (may already exist): {e}")
    else:
        print(f"  WARNING: Schema file not found at {SCHEMA_SQL}")

    # 4. Ensure models directory exists (for Silero VAD)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Models directory ready at {MODELS_DIR}")

    # 5. Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 6. Stop old dashboard if running on port 4096
    uid = os.getuid()
    old_service = f"gui/{uid}/{OLD_DASHBOARD_PLIST}"
    result = _run(["launchctl", "bootout", old_service], timeout=10)
    if result.returncode == 0:
        print("  Stopped old dashboard service")
        time.sleep(1)
    else:
        print("  Old dashboard not running (OK)")

    # 7. Deploy LaunchAgent from template
    if not TEMPLATE_PATH.exists():
        print(f"  ERROR: Plist template not found at {TEMPLATE_PATH}")
        return False

    template = TEMPLATE_PATH.read_text()
    plist_content = template.replace("__HOME__", str(HOME))
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    print(f"  Deployed plist to {PLIST_PATH}")

    # 8. Start Qareen
    domain = f"gui/{uid}"
    service = f"gui/{uid}/{PLIST_NAME}"

    # Bootout first (ignore failure — may not be registered)
    _run(["launchctl", "bootout", service], timeout=10)
    time.sleep(1)

    # Bootstrap
    result = _run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], timeout=10)
    if result.returncode != 0:
        print(f"  WARNING: bootstrap returned {result.returncode}: {result.stderr}")

    # Kickstart. `-k` can block for longer than our timeout while an old
    # instance drains before the new one binds the port — that's not a
    # failure, just launchctl taking its time. A TimeoutExpired here must
    # not fail the migration; the health poll below is the actual success
    # criterion (same drain-blocking pattern fixed in
    # 056_n8n_service.py — see that migration's comment for the incident).
    try:
        _run(["launchctl", "kickstart", "-k", service], timeout=10)
        print("  Qareen LaunchAgent started")
    except subprocess.TimeoutExpired:
        print("  launchctl kickstart timed out (old instance likely still draining) — continuing to health check")

    # 9. Wait for health (up to 60s — kickstart -k above may still be
    # draining the old instance)
    print("  Waiting for Qareen to become healthy...")
    for i in range(30):
        time.sleep(2)
        if _is_healthy():
            print(f"  Qareen healthy after {(i + 1) * 2}s on port 4096")
            return True

    if _port_open(4096):
        # Bound but not answering /api/health yet — legitimately slow cold
        # start (pip install just ran, first boot initializes state).
        # Reconcile owns ongoing monitoring past this point, so this is
        # success, not failure.
        print("  WARNING: Qareen not healthy after 60s, but port 4096 is bound —")
        print("  likely still starting. Check ~/.aos/logs/qareen.err.log.")
        return True

    # Not bound at all — the process never came up (crash loop, bad venv,
    # missing dep). Reconcile can re-kickstart a healthy install that
    # drifted, but it can't heal a binary that never started; a False
    # here is correct even though it stops the migration batch — a
    # machine where Qareen can't start needs a human, not a watermark
    # that silently advanced past a service that was never running.
    print("  ERROR: Qareen not healthy after 60s and port 4096 is not bound —")
    print("  the process failed to start. Check ~/.aos/logs/qareen.err.log.")
    return False
