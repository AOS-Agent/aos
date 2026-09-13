"""
Migration 112: sentinel, converse and envoy ship off by default (v0.7.7).

The three autonomous-comms arms — Sentinel (reads iMessage, drafts
follow-through on commitments), Converse (live multi-turn sessions), Envoy
(runs delegated outbound conversations with third parties) — are the most
personal surface AOS has, and the one whose failure mode is a message actually
sent to another human. Autonomous comms becomes a future Qren arm; until then it
is not a thing to leave switched on, on a machine that never asked for it.

So they become opt-in on every machine:

  1. Each name is recorded in ~/.aos/config/services.yaml `disabled:`, the
     declaration reconcile reads.
  2. A loaded LaunchAgent is booted out. The plist is left on disk — unlike
     work-runner, these have no one-command re-enable path, so deleting the
     plist would turn "off" into "gone" and make opting back in a support
     question. Booted out + declared off is reversible by editing one file.

**Respects an explicit opt-in.** A name listed under `enabled:` in
services.yaml is the operator's own declaration and is skipped entirely — not
disabled here, and not re-disabled on any later run or by the
default_off_services reconcile check. An operator who runs Sentinel keeps
Sentinel.

Envoy has no service manifest and no plist template (it exists only on machines
that adopted it by hand), so on most machines steps 1–2 are a no-op beyond
recording the intent — which is the point: the declaration is what makes the
default true if the service ever appears.

Idempotent: check() passes once every arm is either recorded off or opted in.
Reversible: remove the name from `disabled:` (or add it to `enabled:`) and
reload the plist.
"""

from __future__ import annotations

DESCRIPTION = "sentinel/converse/envoy off by default (autonomous comms → Qren)"

import os
import subprocess
import sys
from pathlib import Path

ARMS = ("sentinel", "converse", "envoy")
LABELS = {name: f"com.aos.{name}" for name in ARMS}


# Resolved on every call, never captured at import — see default_off.py's own
# docstring and migration 111's. Only used for the print in up(); the actual
# write goes through disable_service(), which resolves Path.home() itself.
def _services_config_path() -> Path:
    return Path.home() / ".aos" / "config" / "services.yaml"


sys.path.insert(0, str(Path.home() / "aos" / "core" / "infra" / "lib"))
try:
    from default_off import disable_service, is_opted_in, needs_disabling
except Exception:  # noqa: BLE001 — pre-update tree
    disable_service = None
    is_opted_in = None
    needs_disabling = None


def _loaded(label: str) -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _bootout(label: str) -> bool:
    try:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
            capture_output=True, timeout=30,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def check() -> bool:
    """Applied when no arm is left undeclared, and none is still loaded."""
    if needs_disabling is None:
        return False
    if needs_disabling(ARMS):
        return False
    for name in ARMS:
        if is_opted_in(name):
            continue
        if _loaded(LABELS[name]):
            return False
    return True


def up() -> bool:
    if disable_service is None:
        print("  ⚠ default_off lib unavailable — skipping (nothing disabled)")
        return True

    for name in ARMS:
        label = LABELS[name]
        if is_opted_in(name):
            print(f"  ✓ {name}: explicitly opted in (services.yaml `enabled:`) — left as-is")
            continue

        added = disable_service(name)
        state = "recorded off" if added else "already recorded off"

        if _loaded(label):
            _bootout(label)
            print(f"  ✓ {name}: {state}, booted out {label}")
        else:
            print(f"  ✓ {name}: {state} (not loaded)")

    print(f"     Declaration: {_services_config_path()}")
    print("     Opt back in: list the name under `enabled:` and reload its plist")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 112 already applied" if check() else ("Done" if up() else "Failed"))
