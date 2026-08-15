"""cmux socket control reconcile check.

Invariant: cmux accepts socket commands from callers outside cmux.

`aos start` — the primary way into the system, and the last thing a fresh
install runs — is always an outside caller. cmux's ``socketControlMode``
defaults to ``"cmuxOnly"``, which refuses exactly that kind of caller. Under the
default, `aos start` cannot open a workspace at all and falls back to running
Claude Code in whatever terminal invoked it. On a fresh Mac that meant the
operator watched cmux launch and then got onboarded in Terminal instead.

Migration 099 and the installer set this. This check exists because the setting
lives in the operator's own ``~/.config/cmux/cmux.json`` — a file they edit, a
file cmux's settings UI rewrites, and a file that gets restored from backups.
Drift here is silent: nothing errors, the terminal just stops being the terminal.

This check auto-fixes. The edit is surgical (comments and formatting preserved,
backup written first) and it only ever widens socket access from the default to
"automation" — it never touches a mode the operator deliberately chose, and it
never removes anything.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status, aos_installed  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from infra.cmux_config import (  # noqa: E402
    CMUX_BIN,
    CONFIG_FILE,
    SUFFICIENT_MODES,
    current_mode,
    ensure,
)

logger = logging.getLogger(__name__)


class CmuxSocketControlCheck(ReconcileCheck):
    name = "cmux_socket_control"
    description = (
        "Verifies cmux accepts socket control from outside cmux, so `aos start` "
        "opens a cmux workspace instead of falling back to the calling terminal."
    )

    # Cheap (one small file read) and the failure it catches breaks the way in,
    # so don't make the operator wait for the next release deploy to repair it.
    periodic_fix = True

    @staticmethod
    def _cmux_present() -> bool:
        return CMUX_BIN.exists() or shutil.which("cmux") is not None

    def precondition(self) -> bool:
        """Only meaningful once AOS is installed and cmux is on the machine."""
        return aos_installed() and self._cmux_present()

    def check(self) -> bool:
        return current_mode() in SUFFICIENT_MODES

    def fix(self) -> CheckResult:
        was = current_mode() or "unset (cmux default: cmuxOnly)"
        try:
            changed, status = ensure()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("cmux socket control fix failed")
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message="Could not configure cmux socket control",
                detail=str(exc),
                notify=True,
            )

        if current_mode() in SUFFICIENT_MODES:
            return CheckResult(
                name=self.name,
                status=Status.FIXED if changed else Status.OK,
                message=status,
                detail=f"{CONFIG_FILE} — was: {was}",
            )

        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message="cmux refuses socket control from outside cmux",
            detail=(
                f"{CONFIG_FILE} could not be updated automatically ({status}). "
                'Set automation.socketControlMode to "automation", then run '
                "`cmux reload-config`. Until then `aos start` will open Claude "
                "Code in the calling terminal rather than in cmux."
            ),
            notify=True,
        )
