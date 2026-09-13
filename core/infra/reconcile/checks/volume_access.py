"""Reconcile check: this machine's configured external data volume must be
readable AND writable.

Invariant: IF this machine's storage policy declares an external data drive
(`config/storage.yaml` `data_drive` — the same key `storage_layout` reads),
THEN that volume is mounted and this process can list it and write/read-back
a canary file.

Why this exists (aos#141 / aos#176 / aos#186): macOS TCC silently revokes
the "Removable Volumes" grant when the granting app (cmux, Claude,
terminal) updates. Every path under ~/vault and ~/project then returns
EPERM — which agents historically misread as "the folder is empty",
a corruption-grade failure mode (hygiene sweeps marking live work stale,
sessions concluding files are gone). Two machine-wide outages (2026-07-14
main session, 2026-07-16 bridge sessions) before this canary shipped.

aos#2343: the volume path used to be hardcoded to `/Volumes/AOS-X`, so a
machine with no external data drive at all (vault + projects on the
internal disk — Faisal's Mini) got this NOTIFY on every single cycle,
forever, with no operator action that could ever clear it. A machine that
never declared an external data drive has nothing for this check to
verify, which is an OK, not a permanently broken invariant. A machine
that DOES declare one and can't reach it is exactly the aos#141 failure
mode above, unchanged.

This check runs on the periodic reconcile cadence (30 min), so a revoked
grant is caught within the half hour instead of by a confused agent.
fix() cannot re-grant TCC (that is a GUI-only operator action) — it
notifies loudly with the exact recovery steps instead.
"""

import os
import time
from pathlib import Path

from base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
STORAGE_CONFIG = HOME / "aos" / "config" / "storage.yaml"

RECOVERY = (
    "Data volume unreadable — likely a macOS permission revoke after an "
    "app update. Fix (2 min): System Settings → Privacy & Security → "
    "Files and Folders → your terminal app (cmux/Terminal) → enable "
    "Removable Volumes. Or run: tccutil reset SystemPolicyRemovableVolumes "
    "— then relaunch the app and re-approve the prompt. Until fixed, "
    "agents must treat vault/project reads as UNRELIABLE, not empty."
)


def _configured_data_drive() -> str:
    """The external data-drive path this machine's storage policy
    declares, or "" when none is declared.

    `config/storage.yaml`'s `data_drive` key is the single source of
    truth — `storage_layout` reads the same key to decide whether local
    directories need relocating. A machine with no such key (or no file
    at all) simply has no external data volume, and that is not this
    check's finding to make.
    """
    if not STORAGE_CONFIG.exists():
        return ""
    try:
        import yaml
        data = yaml.safe_load(STORAGE_CONFIG.read_text()) or {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return (data.get("data_drive") or "").strip()


class VolumeAccessCheck(ReconcileCheck):
    name = "volume_access"
    description = "Configured external data volume readable + writable (TCC canary)"

    def _volume(self):
        """This machine's configured data volume, or None when it has
        none declared."""
        drive = _configured_data_drive()
        return Path(drive) if drive else None

    def check(self) -> bool:
        volume = self._volume()
        if volume is None:
            return True  # nothing declared — not this check's concern
        return self._probe(volume)

    @staticmethod
    def _probe(volume: Path) -> bool:
        # Not mounted is a different failure than TCC-revoked, but both
        # mean the data layer is gone — fail either way and let the
        # message distinguish.
        if not volume.exists():
            return False
        try:
            # Read probe: listing must succeed and a configured data
            # volume is never legitimately empty (vault/ and project/
            # live here).
            entries = os.listdir(volume)
            if not entries:
                return False
            # Write/read-back probe: TCC can allow stat but deny open.
            canary_dir = volume / ".aos-canary"
            canary_dir.mkdir(exist_ok=True)
            token = str(time.time_ns())
            probe = canary_dir / "canary.txt"
            probe.write_text(token)
            return probe.read_text() == token
        except (PermissionError, OSError):
            return False

    def fix(self) -> CheckResult:
        volume = self._volume()
        if volume is None:
            # Nothing declared — never invented a finding to notify about.
            return CheckResult(
                self.name,
                Status.OK,
                "no external data volume configured",
            )

        # No programmatic fix exists — TCC grants are GUI-only. Notify
        # loudly with recovery steps; notify=True routes to Telegram.
        mounted = volume.exists()
        msg = (
            f"{volume} not mounted — data layer offline"
            if not mounted
            else f"{volume} mounted but NOT accessible (TCC permission revoked?)"
        )
        return CheckResult(
            self.name,
            Status.NOTIFY,
            msg,
            detail=RECOVERY,
            notify=True,
        )
