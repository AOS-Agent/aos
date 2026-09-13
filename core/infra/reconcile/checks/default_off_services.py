"""
Invariant: the default-off services stay declared off.

Three services ship off — the autonomous-comms arms `sentinel`, `converse` and
`envoy`. Migration 112 records that on each machine by writing the names into
~/.aos/config/services.yaml under `disabled:`. (`work-runner` was the fourth,
recorded by migration 111; v0.7.7 deleted the service and migration 124 clears
its declaration, so this check no longer carries the name — re-declaring a
service that cannot run is drift, not an invariant.)

That declaration is load-bearing, and it is a plain text file the operator
edits. If the name goes missing — a hand-edit, a merge, a restored config from
an older backup — nothing else notices: ServiceLoadedCheck reads the same file,
so an absent opt-out reads as "this service should be running" and the arm the
operator was told is off quietly starts answering messages again. Migrations
run once and cannot catch that; this check runs every cycle and can.

Deliberately narrow:
  - It restores the *declaration*, never the process. Stopping a running
    service is ServiceLoadedCheck's job, driven off the very file this repairs.
  - A name under `enabled:` is the operator's explicit opt-in and is skipped
    entirely. This check must never be the thing that turns Sentinel off under
    an operator who asked for it — that is the failure the opt-in list exists
    to prevent, and re-adding the name here would reintroduce it every 30
    minutes.
  - PyYAML missing ⇒ precondition fails ⇒ SKIP, not a green tick on a file it
    could not read.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.default_off import (
    DEFAULT_OFF,
    SERVICES_CONFIG,
    disable_service,
    needs_disabling,
)


class DefaultOffServicesCheck(ReconcileCheck):
    name = "default_off_services"
    description = "v0.8.0 default-off services remain declared off (unless opted in)"

    # Cheap and file-local, and the window where a missing declaration means a
    # comms arm is live is exactly the window worth closing early.
    periodic_fix = True

    def precondition(self) -> bool:
        if not super().precondition():
            return False
        try:
            import yaml  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return SERVICES_CONFIG.parent.exists()

    def check(self) -> bool:
        return not needs_disabling(DEFAULT_OFF)

    def fix(self) -> CheckResult:
        missing = needs_disabling(DEFAULT_OFF)
        restored = [n for n in missing if disable_service(n)]
        if not restored:
            return CheckResult(
                self.name, Status.NOTIFY,
                f"Could not record default-off services: {', '.join(missing)}",
                detail=str(SERVICES_CONFIG),
            )
        return CheckResult(
            self.name, Status.FIXED,
            f"Re-declared default-off service(s): {', '.join(restored)}",
            detail=f"{SERVICES_CONFIG} — opt in by listing a name under `enabled:`",
        )
