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

`launchctl kickstart -k` can block past a short subprocess timeout while an
old instance drains before the new one starts — that is not a failure, just
launchctl taking its time (the exact drain-blocking shape the v0.6.4 hotfix
fixed in migrations 054/056). An unguarded kickstart raised TimeoutExpired
out of up(), the runner's generic `except Exception` logged the migration as
failed even though the service was healthy seconds later, and the re-run
no-op'd past it (check() saw the plist already deployed). So the kickstart is
wrapped in try/except TimeoutExpired and the real success criterion is a
health poll. Sentinel binds no HTTP port, so "healthy" = launchctl reports the
job running with a pid; the pid-present/absent split after 60s mirrors 054/056's
port-bound/not-bound tail.
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


def _service_pid() -> int | None:
    """Return the running pid of com.aos.sentinel, or None if not running.

    Sentinel has no HTTP port, so a live pid is the health signal. `launchctl
    list <label>` prints a property dict with a `"PID" = <n>;` line only while
    the job is actually running (loaded-but-not-started, or not loaded, has no
    PID line / non-zero exit).
    """
    try:
        result = _run(["launchctl", "list", PLIST_NAME], timeout=5)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if '"PID"' in line:
            digits = "".join(c for c in line if c.isdigit())
            return int(digits) if digits else None
    return None


def _is_running() -> bool:
    """True when launchctl reports a live pid for the Sentinel job."""
    return _service_pid() is not None


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

    # Kickstart. `-k` can block past our timeout while an old instance drains
    # before the new one starts — not a failure, just launchctl taking its
    # time. A TimeoutExpired here must NOT fail the migration; the health poll
    # below is the actual success criterion (the 054/056 drain-blocking fix).
    try:
        _run(["launchctl", "kickstart", "-k", service])
        print("  ✓ Sentinel LaunchAgent kickstarted")
    except subprocess.TimeoutExpired:
        print("  ⚠ launchctl kickstart timed out (old instance likely still draining) — continuing to health check")

    # Wait for the service to report a live pid (up to 60s — kickstart -k may
    # still be draining the old instance).
    print("  Waiting for Sentinel to report a running pid...")
    for i in range(30):
        time.sleep(2)
        if _is_running():
            print(f"  ✓ Sentinel running after {(i + 1) * 2}s")
            return True

    # Poll exhausted. Split on pid presence, mirroring 054/056's port tail.
    if _service_pid() is not None:
        # A pid appeared right at the boundary — slow start. The reconcile
        # check (SentinelPlistDriftCheck) owns ongoing plist correctness and
        # KeepAlive owns process liveness from here, so this is success.
        print("  ⚠ Sentinel slow to report but pid now present — treating as started")
        return True

    # No pid after 60s — the service never came up (crash loop, bad import,
    # missing dep). KeepAlive/reconcile can restart a job that drifted but
    # cannot heal a process that never starts; a False here correctly stops
    # the batch for a human rather than silently advancing the watermark past
    # a service that was never actually running.
    print("  ✗ Sentinel not running after 60s and no pid reported —")
    print(f"    the service failed to start. Check {LOG_DIR}/launchagent.err.log")
    return False


if __name__ == "__main__":
    if check():
        print("Migration 071 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
