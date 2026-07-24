"""
Invariant: The machine restarts automatically after a power failure.

`pmset autorestart 1` is what brings a headless Mac back up after an outage.
macOS updates and NVRAM/SMC resets are known to silently revert it, which is
invisible until the next power cut leaves the machine dark.

This is a NOTIFY-only check — `pmset -a` requires sudo, and reconcile runs
unattended with no TTY to prompt on.

Fix command: `sudo pmset -a autorestart 1`

Skipped on battery-equipped Macs (laptops), where the internal battery already
rides out brief outages and the setting has different semantics.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

FIX_COMMAND = "sudo pmset -a autorestart 1"


def _pmset(*args) -> str:
    """Run pmset and return stdout, or '' on any failure."""
    try:
        result = subprocess.run(
            ["pmset", *args],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _has_internal_battery() -> bool:
    """True on laptops — `pmset -g batt` lists an InternalBattery."""
    return "InternalBattery" in _pmset("-g", "batt")


def _autorestart_value() -> Optional[str]:
    """Parse the `autorestart` row from `pmset -g`. None if absent."""
    for line in _pmset("-g").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "autorestart":
            return parts[1]
    return None


class PowerAutorestartCheck(ReconcileCheck):
    name = "power_autorestart"
    description = "Mac powers back on automatically after a power failure"

    def __init__(self) -> None:
        self._skip_reason = None
        self._value = None

    def check(self) -> bool:
        if shutil.which("pmset") is None:
            self._skip_reason = "pmset not available — not a macOS host"
            return False
        if _has_internal_battery():
            self._skip_reason = "Laptop (internal battery) — autorestart not applicable"
            return False
        self._value = _autorestart_value()
        if self._value is None:
            self._skip_reason = "pmset does not report autorestart on this hardware"
            return False
        return self._value == "1"

    def fix(self) -> CheckResult:
        if self._skip_reason:
            return CheckResult(self.name, Status.SKIP, self._skip_reason)

        return CheckResult(
            self.name,
            Status.NOTIFY,
            "Auto-restart after power failure is OFF — this Mac will stay down after an outage",
            detail=(
                f"pmset reports: autorestart {self._value}"
                "\nExpected:      autorestart 1"
                "\n\nFix (requires your password):\n  "
                f"{FIX_COMMAND}"
                "\n\nVerify with:\n  pmset -g | grep autorestart"
                "\n\nmacOS updates and NVRAM/SMC resets can revert this silently."
            ),
            notify=True,
        )
