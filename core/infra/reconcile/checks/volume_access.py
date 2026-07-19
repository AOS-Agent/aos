"""Reconcile check: the AOS-X data volume must be readable AND writable.

Invariant: /Volumes/AOS-X (the data layer — vault, projects, caches) is
mounted and this process can list it and write/read-back a canary file.

Why this exists (aos#141 / aos#176 / aos#186): macOS TCC silently revokes
the "Removable Volumes" grant when the granting app (cmux, Claude,
terminal) updates. Every path under ~/vault and ~/project then returns
EPERM — which agents historically misread as "the folder is empty",
a corruption-grade failure mode (hygiene sweeps marking live work stale,
sessions concluding files are gone). Two machine-wide outages (2026-07-14
main session, 2026-07-16 bridge sessions) before this canary shipped.

This check runs on the periodic reconcile cadence (30 min), so a revoked
grant is caught within the half hour instead of by a confused agent.
fix() cannot re-grant TCC (that is a GUI-only operator action) — it
notifies loudly with the exact recovery steps instead.
"""

import os
import time
from pathlib import Path

from base import CheckResult, ReconcileCheck, Status

VOLUME = Path("/Volumes/AOS-X")
CANARY_DIR = VOLUME / ".aos-canary"

RECOVERY = (
    "AOS-X unreadable — likely a macOS permission revoke after an app "
    "update. Fix (2 min): System Settings → Privacy & Security → Files "
    "and Folders → your terminal app (cmux/Terminal) → enable Removable "
    "Volumes. Or run: tccutil reset SystemPolicyRemovableVolumes — then "
    "relaunch the app and re-approve the prompt. Until fixed, agents "
    "must treat vault/project reads as UNRELIABLE, not empty."
)


class VolumeAccessCheck(ReconcileCheck):
    name = "volume_access"
    description = "AOS-X data volume readable + writable (TCC canary)"

    def check(self) -> bool:
        # Not mounted is a different failure than TCC-revoked, but both
        # mean the data layer is gone — fail either way and let the
        # message distinguish.
        if not VOLUME.exists():
            return False
        try:
            # Read probe: listing must succeed and the volume is never
            # legitimately empty (vault/ and project/ live here).
            entries = os.listdir(VOLUME)
            if not entries:
                return False
            # Write/read-back probe: TCC can allow stat but deny open.
            CANARY_DIR.mkdir(exist_ok=True)
            token = str(time.time_ns())
            probe = CANARY_DIR / "canary.txt"
            probe.write_text(token)
            return probe.read_text() == token
        except (PermissionError, OSError):
            return False

    def fix(self) -> CheckResult:
        # No programmatic fix exists — TCC grants are GUI-only. Notify
        # loudly with recovery steps; notify=True routes to Telegram.
        mounted = VOLUME.exists()
        msg = (
            "AOS-X volume not mounted — data layer offline"
            if not mounted
            else "AOS-X mounted but NOT accessible (TCC permission revoked?)"
        )
        return CheckResult(
            self.name,
            Status.NOTIFY,
            msg,
            detail=RECOVERY,
            notify=True,
        )
