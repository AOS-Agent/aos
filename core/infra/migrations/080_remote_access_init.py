"""
Migration 080: Qareen Remote Access — schema + host-bind env var.

Phase-1 of the Qareen Remote Access feature (link a Cloudflare-hosted
domain so local Qareen is reachable at aos.<domain> behind Cloudflare
Access). This migration ships the two instance-impacting pieces that the
framework code assumes exist:

1. The ``remote_access`` state table in ~/.aos/data/qareen.db. Single
   logical row (id='singleton') holding CF metadata only — NO secrets
   (the CF API token + cloudflared run-token live ONLY in Keychain via
   agent-secret). allowed_emails is JSON TEXT.

2. The deployed Qareen LaunchAgent gains the ``AOS_QAREEN_HOST`` env var.
   The framework template (com.aos.qareen.plist.template) already declares
   ``AOS_QAREEN_HOST=0.0.0.0`` (the default LAN/Tailscale bind). This
   migration regenerates the DEPLOYED plist from that template (reusing the
   canonical __HOME__ substitution from migrations 012/026) and restarts the
   service so the env var takes effect. At connect-time TunnelManager rebinds
   to 127.0.0.1 by rewriting the deployed plist; this migration only
   establishes the documented default + the env var the __main__ path reads.

It does NOT deploy the cloudflared tunnel plist — that is generated at user
connect-time by TunnelManager. It only detects whether cloudflared is
installed and prints install guidance if absent (non-fatal).

Idempotent: CREATE TABLE IF NOT EXISTS, plist regeneration is a deterministic
re-write, launchctl lifecycle tolerates re-runs.

Kickstart safety: ``launchctl kickstart -k`` can block past a short subprocess
timeout while the old Qareen instance drains before the new one binds. An
unguarded kickstart would raise TimeoutExpired out of up(), the runner's
generic ``except Exception`` would log the migration failed even though the
service is healthy seconds later, and the re-run would no-op past it (check()
sees the env var already in the plist). So the kickstart is wrapped in
try/except TimeoutExpired and the real success criterion is a port-4096 health
poll — mirroring migrations 054/056/071.
"""

DESCRIPTION = "Qareen Remote Access: remote_access table + AOS_QAREEN_HOST bind env var"

import os
import shutil
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"

DB_PATH = HOME / ".aos" / "data" / "qareen.db"

PLIST_NAME = "com.aos.qareen"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
TEMPLATE_PATH = AOS_ROOT / "config" / "launchagents" / f"{PLIST_NAME}.plist.template"

HOST_ENV_KEY = "AOS_QAREEN_HOST"
QAREEN_PORT = 4096


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if something is listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def check() -> bool:
    """Applied if the remote_access table exists AND the deployed Qareen
    plist carries the AOS_QAREEN_HOST env var."""
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not _table_exists(conn, "remote_access"):
            return False
    finally:
        conn.close()

    if not PLIST_PATH.exists():
        return False
    return HOST_ENV_KEY in PLIST_PATH.read_text()


def up() -> bool:
    """Create the remote_access table, regenerate the deployed Qareen plist
    (gaining AOS_QAREEN_HOST), and detect cloudflared."""

    # 1. remote_access state table (metadata only; secrets stay in Keychain).
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS remote_access (
                id             TEXT PRIMARY KEY DEFAULT 'singleton',
                status         TEXT NOT NULL DEFAULT 'disconnected',
                hostname       TEXT,
                domain         TEXT,
                zone_id        TEXT,
                account_id     TEXT,
                tunnel_id      TEXT,
                dns_record_id  TEXT,
                access_app_id  TEXT,
                access_aud     TEXT,
                policy_id      TEXT,
                idp_id         TEXT,
                allowed_emails TEXT,
                created_at     TEXT,
                updated_at     TEXT,
                error_message  TEXT
            );
            """
        )
        conn.commit()
        print("  ✓ remote_access table ensured")
    except Exception as e:
        print(f"  ✗ remote_access table creation failed: {e}")
        return False
    finally:
        conn.close()

    # 2. Regenerate the deployed Qareen plist from the framework template so
    #    it gains AOS_QAREEN_HOST, then bootout → bootstrap → kickstart.
    if not TEMPLATE_PATH.exists():
        print(f"  ✗ Plist template not found at {TEMPLATE_PATH}")
        return False

    # Canonical substitution (matches migrations 012 + 026).
    plist_content = TEMPLATE_PATH.read_text().replace("__HOME__", str(HOME))
    if HOST_ENV_KEY not in plist_content:
        # Template should already declare it (shipped alongside this migration).
        # Don't inject here — the template is the source of truth — but warn.
        print(
            f"  ⚠ template does not declare {HOST_ENV_KEY}; "
            "deployed plist will lack the bind env var"
        )

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    print(f"  ✓ Regenerated deployed plist at {PLIST_PATH}")

    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"gui/{uid}/{PLIST_NAME}"

    # Bootout first (ignore failure — may not be registered).
    _run(["launchctl", "bootout", service])
    time.sleep(1)

    result = _run(["launchctl", "bootstrap", domain, str(PLIST_PATH)])
    if result.returncode != 0:
        print(f"  ⚠ bootstrap returned {result.returncode}: {result.stderr.strip()}")

    # Kickstart. `-k` can block past our timeout while an old instance drains
    # before the new one binds — not a failure, just launchctl taking its time.
    # A TimeoutExpired here must NOT fail the migration; the health poll below
    # is the actual success criterion (the 054/056/071 drain-blocking fix).
    try:
        _run(["launchctl", "kickstart", "-k", service])
        print("  ✓ Qareen LaunchAgent kickstarted")
    except subprocess.TimeoutExpired:
        print("  ⚠ launchctl kickstart timed out (old instance likely still draining) — continuing to health check")

    # Wait for Qareen to bind its port again (up to 60s — kickstart -k may
    # still be draining the old instance).
    print(f"  Waiting for Qareen to bind 127.0.0.1:{QAREEN_PORT}...")
    for i in range(30):
        time.sleep(2)
        if _port_open(QAREEN_PORT):
            print(f"  ✓ Qareen listening after {(i + 1) * 2}s")
            break
    else:
        # Poll exhausted. Split, mirroring 054/056/071's port/pid tail: a port
        # that reappears right at the boundary is a slow start (KeepAlive owns
        # liveness from here); no port after 60s means the service never came
        # up (crash loop, bad venv) — a False correctly stops the batch for a
        # human rather than silently advancing the watermark past a service
        # that was never actually running.
        if _port_open(QAREEN_PORT):
            print("  ⚠ Qareen slow to bind but port now open — treating as started")
        else:
            print(f"  ✗ Qareen not listening on {QAREEN_PORT} after 60s — the")
            print("    service failed to restart. Check ~/.aos/logs/qareen.err.log")
            return False

    # 3. Detect cloudflared (needed at connect-time; non-fatal here).
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        for candidate in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
            if Path(candidate).exists():
                cloudflared = candidate
                break
    if cloudflared:
        print(f"  ✓ cloudflared found at {cloudflared}")
    else:
        print(
            "  ⚠ cloudflared not found — install before connecting remote access:\n"
            "      brew install cloudflared"
        )

    return True


if __name__ == "__main__":
    if check():
        print("Migration 080 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
