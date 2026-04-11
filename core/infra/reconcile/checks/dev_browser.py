"""
Invariant: dev-browser binary is installed globally.

Used by Claude Code's chrome integration for browser automation.
Installs via npm if missing.
"""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


class DevBrowserCheck(ReconcileCheck):
    name = "dev_browser"
    description = "dev-browser binary installed globally via npm"

    def check(self) -> bool:
        return shutil.which("dev-browser") is not None

    def fix(self) -> CheckResult:
        try:
            result = subprocess.run(
                ["npm", "i", "-g", "dev-browser"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return CheckResult(
                    self.name, Status.NOTIFY,
                    "Failed to install dev-browser via npm",
                    detail=result.stderr.strip(),
                    notify=True,
                )
            return CheckResult(
                self.name, Status.FIXED,
                "Installed dev-browser globally via npm",
            )
        except FileNotFoundError:
            return CheckResult(
                self.name, Status.NOTIFY,
                "npm not found — cannot install dev-browser",
                detail="Ensure Node.js and npm are installed",
                notify=True,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                self.name, Status.NOTIFY,
                "npm install timed out after 120s",
                notify=True,
            )
