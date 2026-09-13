"""
Migration 111: work-runner is off by default, fleet-wide (v0.8.0 freeze).

The work runner (905 lines + a resident LaunchAgent) has **zero rows in
`task_runs`, ever** — built, deployed, KeepAlive'd, and never once used to
delegate a task (work-engine audit 2026-08-20 §5, §7). Migration 092 shipped it
"OFF by default", but only in the sense that a *fresh* machine wouldn't deploy
it; machines where the operator once ran `work runner enable`, or where an
older install path loaded the plist, have been running it since.

This migration makes "off" true on every machine that already has it:

  1. Records `work-runner` in the operator opt-out (~/.aos/config/services.yaml
     `disabled:`), which is the one declaration reconcile reads — without it,
     ServiceLoadedCheck could legitimately bring the service back.
  2. Boots out com.aos.work-runner if loaded, and removes the deployed plist so
     it does not come back at next login.
  3. Flips `enabled: false` in ~/.aos/config/work-runner.yaml.

**Respects an explicit opt-in.** An operator who wants the runner writes the
service name under `enabled:` in ~/.aos/config/services.yaml. That list is this
migration's veto: a name there is never auto-disabled, here or on a re-run. The
opt-in is a declaration the operator makes once and updates never override —
the same contract migration 105 established for `disabled:`.

Reversible: `work runner enable` re-deploys and re-loads the plist. Remove the
name from `disabled:` (or add it to `enabled:`) and reconcile stops holding it
down. No data is touched — `task_runs` (empty) and the config file both stay.

Idempotent: check() passes once the name is recorded and the plist is gone.

Ships with core/infra/reconcile/checks/default_off_services.py, which keeps the
declaration true on later runs.
"""

from __future__ import annotations

DESCRIPTION = "work-runner off by default fleet-wide (0 recorded runs, ever)"

import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
SERVICES_CONFIG = HOME / ".aos" / "config" / "services.yaml"
RUNNER_CONFIG = HOME / ".aos" / "config" / "work-runner.yaml"
LABEL = "com.aos.work-runner"
PLIST = HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"

SERVICE = "work-runner"

# Shared with migration 112 and the reconcile check — one implementation of
# "record this service as default-off unless the operator opted in".
sys.path.insert(0, str(AOS_ROOT / "core" / "infra" / "lib"))
try:
    from default_off import disable_service, is_opted_in, is_recorded_off
except Exception:  # noqa: BLE001 — pre-update tree; fall back to no-op guards
    disable_service = None
    is_opted_in = None
    is_recorded_off = None


def _loaded() -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _flip_runner_config() -> bool:
    """Set `enabled: false` in work-runner.yaml. True if the file now says so."""
    if not RUNNER_CONFIG.exists():
        return True
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return True  # can't parse; the opt-out + missing plist already hold it off
    try:
        raw = yaml.safe_load(RUNNER_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(raw, dict) or raw.get("enabled") is False:
        return True
    raw["enabled"] = False
    RUNNER_CONFIG.write_text(yaml.safe_dump(raw, sort_keys=False))
    return True


def _runner_config_enabled() -> bool:
    if not RUNNER_CONFIG.exists():
        return False
    try:
        import yaml
        raw = yaml.safe_load(RUNNER_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return False
    return isinstance(raw, dict) and raw.get("enabled") is True


def check() -> bool:
    """Applied when the runner is recorded off (or explicitly opted in)."""
    if is_opted_in is None:
        return False
    if is_opted_in(SERVICE):
        return True  # operator's declaration wins — nothing for us to do
    if not is_recorded_off(SERVICE):
        return False
    if PLIST.exists() or _loaded():
        return False
    return not _runner_config_enabled()


def up() -> bool:
    if disable_service is None:
        print("  ⚠ default_off lib unavailable — skipping (nothing disabled)")
        return True

    if is_opted_in(SERVICE):
        print(f"  ✓ {SERVICE} is explicitly opted in (services.yaml `enabled:`) — left running")
        return True

    added = disable_service(SERVICE)
    print(f"  {'✓ Recorded' if added else '·  Already recorded'} {SERVICE} in {SERVICES_CONFIG}")

    if _loaded():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=30,
        )
        print(f"  ✓ Booted out {LABEL}")
    if PLIST.exists():
        PLIST.unlink()
        print(f"  ✓ Removed {PLIST}")

    _flip_runner_config()
    print(f"  ✓ {RUNNER_CONFIG.name}: enabled: false")
    print("     Reversible: `work runner enable` (or list it under `enabled:` in services.yaml)")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 111 already applied" if check() else ("Done" if up() else "Failed"))
