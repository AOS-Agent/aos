"""
Base class and types for reconcile checks.

Each check expresses an invariant — something that should ALWAYS be true
about a correctly-configured AOS installation. Unlike migrations (run once),
reconcile checks run on every update cycle.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(Enum):
    OK = "ok"          # Invariant holds, no action taken
    FIXED = "fixed"    # Was broken, successfully repaired
    SKIP = "skip"      # Cannot verify (missing prereq), logged and moved on
    NOTIFY = "notify"  # Broken but cannot safely auto-fix — operator notified
    ERROR = "error"    # Check itself crashed


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    detail: Optional[str] = None
    notify: bool = False


class ReconcileCheck:
    """Base class for reconcile checks.

    Subclass and implement:
      name: str          — unique ID, never changes
      description: str   — human-readable purpose
      check() -> bool    — True if invariant holds
      fix() -> CheckResult — attempt repair (only called if check() is False)

    periodic_fix: opt-in flag. Full fix-mode runs only on update deploys. A
    check that sets periodic_fix = True is ALSO allowed to fix() on the
    lightweight periodic reconcile (every ~30 min), so a failure it owns doesn't
    have to wait for the next release. Everything else stays report-only there.
    """
    name: str = "unnamed"
    description: str = ""
    periodic_fix: bool = False

    def check(self) -> bool:
        raise NotImplementedError

    def fix(self) -> CheckResult:
        raise NotImplementedError
