"""Dev backend LaunchAgent reconcile check.

Verifies the qareen-dev LaunchAgent plist is installed and loaded so the
dev backend on port 4097 stays alive under launchd. Without this, a
hot-reload deadlock can freeze the dev backend for hours without anyone
noticing — exactly what bit us in Part 8.

The check is NOTIFY-only — it never auto-installs the plist. The
operator runs core/bin/internal/install-qareen-dev-plist to activate.
This follows the "destructive operations require operator approval"
rule: modifying ~/Library/LaunchAgents/ is a visible system change, so
we surface it but never force it.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..base import CheckResult, ReconcileCheck, Status

logger = logging.getLogger(__name__)

LABEL = "com.agent.qareen-dev"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


class DevBackendPlistCheck(ReconcileCheck):
    name = "dev_backend_plist"
    description = (
        "Verifies the qareen-dev LaunchAgent plist is installed and loaded "
        "so the dev backend on port 4097 stays alive under launchd. "
        "Install with core/bin/internal/install-qareen-dev-plist."
    )

    def __init__(self) -> None:
        self._installed = False
        self._loaded = False
        self._reason = ""

    def check(self) -> bool:
        # Is the plist file present?
        self._installed = PLIST_PATH.is_file()
        if not self._installed:
            self._reason = f"plist not found at {PLIST_PATH}"
            return False

        # Is it loaded in launchctl?
        try:
            result = subprocess.run(
                ["launchctl", "list"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                self._reason = "launchctl list failed"
                return False
            self._loaded = LABEL in result.stdout
            if not self._loaded:
                self._reason = "plist present but not loaded in launchctl"
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self._reason = f"launchctl unavailable: {e}"
            return False

        return True

    def fix(self) -> CheckResult:
        """Never auto-fixes — notifies operator to run the installer."""
        if not self._installed:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=(
                    "Dev backend LaunchAgent not installed. Run: "
                    "~/aos/core/bin/internal/install-qareen-dev-plist"
                ),
                detail=self._reason,
                notify=False,  # quiet — dev-only concern
            )
        if not self._loaded:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=(
                    "Dev backend plist installed but not loaded. Run: "
                    f"launchctl load {PLIST_PATH}"
                ),
                detail=self._reason,
                notify=False,
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message="qareen-dev LaunchAgent loaded",
        )
