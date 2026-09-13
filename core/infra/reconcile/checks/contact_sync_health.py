"""
Invariant: contact-sync's AddressBook access failures are surfaced, not silent.

sync_contacts.py (core/engine/comms/sync_contacts.py, the `contact-sync`
cron) found 0 macOS contacts in ~90% of its runs over 4 months (cron audit,
2026-09-13) because a TCC/path failure read identically to "genuinely no
contacts" — both silently returned {"new": 0, "updated": 0, "unchanged": 0}
with exit 0.

The script itself still always exits 0 on a denial — a TCC denial is an
expected, recoverable condition, not a crash, so cron telemetry (cron_runs,
which cron_health.py reads) should not call it "broken" the way a stray
exception would. But that same choice means the generic exit-code-based
cron_health check can never see this failure. sync_contacts.py now records
which of "ok" / "denied" / "not_found" actually happened to
~/.aos/data/contact-sync-status.json on every run. This check reads that
file directly and is the one thing that turns a persistent denial into a
NOTIFY.

"not_found" (no AddressBook-v*.abcddb anywhere) is NOT treated as broken —
a machine that has never configured Contacts at all is not a health problem
to keep alerting about. Only "denied" (a real, actionable TCC/permission
failure) fails this check.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

STATUS_FILE = Path.home() / ".aos" / "data" / "contact-sync-status.json"


def _read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:  # noqa: BLE001 — missing, corrupted, whatever: unobserved
        return {}


class ContactSyncHealthCheck(ReconcileCheck):
    name = "contact_sync_health"
    description = "contact-sync's AddressBook access is actually working, not silently denied"

    def check(self) -> bool:
        status = _read_status()
        if not status:
            return True  # never run yet, or nothing to judge
        return status.get("result") != "denied"

    def fix(self) -> CheckResult:
        status = _read_status()
        if not status or status.get("result") != "denied":
            return CheckResult(
                self.name, Status.OK,
                "contact-sync AddressBook access is fine (or unobserved)",
            )
        return CheckResult(
            self.name, Status.NOTIFY,
            "contact-sync: AddressBook access denied — grant Contacts/Full Disk Access",
            detail=status.get("detail", ""),
            notify=True,
        )
