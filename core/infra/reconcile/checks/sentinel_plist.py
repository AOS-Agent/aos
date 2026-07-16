"""
Invariant: the deployed Sentinel LaunchAgent plist matches its framework
template.

The Sentinel service (com.aos.sentinel) is deployed from
config/launchagents/com.aos.sentinel.plist.template via the canonical
__HOME__ substitution (migrations 012/071). If the deployed plist drifts
away from the template — a manual edit, a bad merge that reintroduces
hardcoded /Users/... paths, or a partial update — Sentinel would run from
a stale definition (wrong working directory, wrong PYTHONPATH, or a
privacy-leaking absolute operator path).

LaunchAgentPythonCheck only validates that referenced Python binaries exist;
it does not compare a plist against its template. This check closes that gap
for the one AOS service that ships from a template today.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.service_ctl import restart_launchagent


class SentinelPlistDriftCheck(ReconcileCheck):
    name = "sentinel_plist_drift"
    description = "Deployed Sentinel plist matches its framework template"

    PLIST_NAME = "com.aos.sentinel"
    HOME = Path.home()
    TEMPLATE_PATH = (
        HOME / "aos" / "config" / "launchagents" / f"{PLIST_NAME}.plist.template"
    )
    PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"

    def _render(self) -> str | None:
        """Render the template with the canonical __HOME__ substitution."""
        if not self.TEMPLATE_PATH.exists():
            return None
        return self.TEMPLATE_PATH.read_text().replace("__HOME__", str(self.HOME))

    def check(self) -> bool:
        expected = self._render()
        if expected is None:
            # No template shipped (shouldn't happen post-update) — nothing to
            # enforce. Treated as OK rather than a false drift alarm.
            return True
        if not self.PLIST_PATH.exists():
            return False
        return self.PLIST_PATH.read_text() == expected

    def fix(self) -> CheckResult:
        expected = self._render()
        if expected is None:
            return CheckResult(
                self.name, Status.SKIP,
                "Sentinel plist template not found — cannot verify drift",
                detail=str(self.TEMPLATE_PATH),
            )

        self.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.PLIST_PATH.write_text(expected)

        # Reload through the shared guarded choke-point so the running service
        # picks up the corrected definition (settle → verify → retry, with
        # lifecycle audit logging). The plist — which is what this check owns —
        # is already corrected on disk regardless; if the reload can't verify
        # (deps not ready), KeepAlive will retry, so this is a note, not a fail.
        ok = restart_launchagent(
            self.PLIST_NAME, self.PLIST_PATH, actor="reconcile:sentinel_plist"
        )
        detail = None if ok else "reload did not verify loaded — plist corrected, KeepAlive will retry"

        return CheckResult(
            self.name, Status.FIXED,
            "Re-rendered Sentinel plist from template and reloaded service",
            detail=detail,
        )
