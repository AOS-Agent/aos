"""
Base class and types for reconcile checks.

Each check expresses an invariant — something that should ALWAYS be true
about a correctly-configured AOS installation. Unlike migrations (run once),
reconcile checks run on every update cycle.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


def aos_installed() -> bool:
    """
    True when there is an AOS installation to make statements about.

    The floor under every check: the framework tree and the instance dir. With
    neither present there is no invariant to verify, so a check that answers
    "fine" is not reporting health — it is reporting that it looked at nothing.
    """
    home = Path.home()
    return (home / "aos").exists() and (home / ".aos").exists()


class Status(Enum):
    OK = "ok"          # Invariant holds, no action taken
    FIXED = "fixed"    # Was broken, successfully repaired
    SKIP = "skip"      # Cannot verify (missing prereq), logged and moved on
    NOTIFY = "notify"  # Broken but cannot safely auto-fix — operator notified
    ERROR = "error"    # Check itself crashed
    # Operator switched this service off. NOT a failure: the invariant is
    # deliberately not being enforced. Distinct from SKIP, which means "could
    # not verify" — this means "was told not to". Without it, a service the
    # operator disabled is indistinguishable from one that died, so reconcile
    # reads intent as drift and restarts it.
    DISABLED = "disabled"


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

    precondition(): what must exist for this check to MEAN anything.

    A check has three possible answers, not two: the invariant holds, the
    invariant is broken, or it could not be evaluated. Collapsing the third into
    the first is how a monitor goes blind — it keeps reporting OK while
    verifying nothing, and the thing it was supposed to watch rots unobserved.

    This is not theoretical. Run the full suite against a completely empty
    machine — no AOS, no vault, no config, no services — and 25 of 34 checks
    reported "invariant holds". They were not checking an empty machine; they
    were failing to notice there was nothing to check. The same shape produced:

      * two ship-check guards that grepped a file moved months earlier —
        `grep -q` on a missing file returns non-zero, so both fell to their else
        branch and printed a green ✓ while reading nothing;
      * `tracker_health` shipping green across 23/23 cron failures because it
        "only checked packs, schema, lock, and credentials, never whether a
        cron had ever succeeded";
      * the `people` package moving out from under three crons, unnoticed for
        three months, because nothing asserted the import still resolved.

    So: return False when the inputs this check reads are absent. The runner
    records SKIP — visible as unverified — instead of a green tick nobody
    earned. Default True, because a check with no external prerequisite is
    always meaningful.
    """
    name: str = "unnamed"
    description: str = ""
    periodic_fix: bool = False

    # The service this check enforces, when it enforces one. Set it and the
    # runner skips this check entirely while the operator has that service
    # disabled — check() and fix() are never called.
    #
    # Deliberately NOT expressed as a precondition() override. precondition is
    # for inputs the check READS; a disabled service is the condition it TESTS,
    # and it would report SKIP ("could not verify") for something that was in
    # fact verified and then deliberately ignored.
    service: Optional[str] = None

    def precondition(self) -> bool:
        """
        True when this check's inputs exist and it can give a real answer.

        Defaults to "an AOS installation exists", which is the floor for every
        check. Override to add a narrower prerequisite — but only for inputs the
        check READS, never for the condition it TESTS. `volume_access` must not
        skip when the AOS-X volume is missing: an unmounted volume is its
        finding, not a missing prerequisite. Skipping there would reintroduce
        exactly the blindness this exists to remove.
        """
        return aos_installed()

    def check(self) -> bool:
        raise NotImplementedError

    def fix(self) -> CheckResult:
        raise NotImplementedError
