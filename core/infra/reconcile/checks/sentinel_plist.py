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

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


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

        uid = os.getuid()
        domain = f"gui/{uid}"
        service = f"gui/{uid}/{self.PLIST_NAME}"

        # Reload so the running service picks up the corrected definition.
        subprocess.run(["launchctl", "bootout", service],
                       capture_output=True, timeout=10)
        time.sleep(1)
        result = subprocess.run(["launchctl", "bootstrap", domain, str(self.PLIST_PATH)],
                                capture_output=True, text=True, timeout=10)

        detail = None
        if result.returncode != 0:
            # Reload can fail if deps aren't ready — plist is corrected on disk
            # and KeepAlive will retry; report but do not fail the check.
            detail = f"bootstrap returned {result.returncode}: {result.stderr.strip()}"

        # kickstart -k can block past the timeout while the old instance drains
        # (the 054/056/071 drain-blocking shape). A TimeoutExpired here must not
        # turn this fix into an ERROR — the plist, which is what this check owns,
        # is already corrected on disk and KeepAlive will restart the job.
        try:
            subprocess.run(["launchctl", "kickstart", "-k", service],
                           capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            note = "kickstart timed out (old instance draining) — plist corrected, KeepAlive will restart"
            detail = f"{detail}; {note}" if detail else note

        return CheckResult(
            self.name, Status.FIXED,
            "Re-rendered Sentinel plist from template and reloaded service",
            detail=detail,
        )
