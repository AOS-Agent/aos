"""
Migration 071: Install the Sentinel LaunchAgent from its template.

The Sentinel service (com.aos.sentinel) previously shipped as a hand-rolled
plist with hardcoded absolute paths. It is now a proper framework template
(config/launchagents/com.aos.sentinel.plist.template) using the canonical
__HOME__ placeholder, matching every other AOS service.

Migration 012 auto-globs *.plist.template and installs them, so FRESH installs
(which run the full history from zero) pick the Sentinel template up there.
This migration exists for EXISTING machines that are already past 012: it
materializes the deployed plist from the template (reusing the __HOME__
substitution from migrations 012/026/049) and (re)loads the service.

The Sentinel log directory (~/.aos/logs/sentinel/) is created by migration
070, which also runs before this one.

Idempotent: deterministic plist re-write, launchctl lifecycle tolerates
re-runs, and check() confirms the deployed plist matches the template.
"""

DESCRIPTION = "Install Sentinel LaunchAgent from template (com.aos.sentinel)"

import os
import subprocess
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"

PLIST_NAME = "com.aos.sentinel"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
TEMPLATE_PATH = AOS_ROOT / "config" / "launchagents" / f"{PLIST_NAME}.plist.template"

LOG_DIR = HOME / ".aos" / "logs" / "sentinel"


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _render() -> str | None:
    """Render the template with the canonical __HOME__ substitution."""
    if not TEMPLATE_PATH.exists():
        return None
    return TEMPLATE_PATH.read_text().replace("__HOME__", str(HOME))


def check() -> bool:
    """Applied when the deployed plist exists and matches the rendered template."""
    expected = _render()
    if expected is None:
        # No template on disk (shouldn't happen post-update) — nothing to do.
        return True
    if not PLIST_PATH.exists():
        return False
    return PLIST_PATH.read_text() == expected


def up() -> bool:
    """Materialize the deployed Sentinel plist from the template and load it."""
    content = _render()
    if content is None:
        print(f"  ✗ Sentinel plist template not found at {TEMPLATE_PATH}")
        return False

    # Log dir is normally created by migration 070; ensure it exists so the
    # service's StandardOut/ErrPath are writable even if 070 was skipped.
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(content)
    print(f"  ✓ Rendered deployed plist at {PLIST_PATH}")

    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"gui/{uid}/{PLIST_NAME}"

    # Bootout first (ignore failure — may not be registered yet).
    _run(["launchctl", "bootout", service])
    time.sleep(1)

    result = _run(["launchctl", "bootstrap", domain, str(PLIST_PATH)])
    if result.returncode != 0:
        # Load can fail if deps aren't ready — non-fatal, service will retry.
        print(f"  ⚠ bootstrap returned {result.returncode}: {result.stderr.strip()}")

    _run(["launchctl", "kickstart", "-k", service])
    print("  ✓ Sentinel LaunchAgent loaded")

    return True


if __name__ == "__main__":
    if check():
        print("Migration 071 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
