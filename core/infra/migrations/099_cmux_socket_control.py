"""
Migration 099: let `aos start` actually open cmux.

Migration 097 made cmux the only AOS terminal surface and pointed `aos start` at
the cmux socket API. What it missed is that cmux's socket API is closed by
default: ``socketControlMode`` defaults to ``"cmuxOnly"``, which only accepts
commands from a caller already inside cmux (one with $CMUX_WORKSPACE_ID /
$CMUX_SURFACE_ID set).

`aos start` is always an outside caller — at the end of an install it runs in
Terminal.app. So every socket call it made was refused, and it fell through to
its "cmux did not respond" branch and ran Claude Code in Terminal instead. From
the operator's seat: cmux visibly launches, and then onboarding starts in the
wrong window. Nothing in AOS had ever written this setting, so this affected
every install.

This migration writes ``automation.socketControlMode: "automation"`` into
~/.config/cmux/cmux.json — surgically, preserving the operator's comments and
formatting, backing the file up first, and leaving any already-permissive mode
(password / allowAll / openAccess / full) exactly as the operator set it.
"""

DESCRIPTION = "Enable cmux socket control so `aos start` opens cmux, not Terminal"

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.cmux_config import (  # noqa: E402
    CMUX_BIN,
    CONFIG_FILE,
    SUFFICIENT_MODES,
    current_mode,
    ensure,
)


def _cmux_present() -> bool:
    return CMUX_BIN.exists() or shutil.which("cmux") is not None


def check() -> bool:
    """Runner contract: True means already applied, skip this migration."""
    if not _cmux_present():
        # No cmux, nothing to configure. Migration 097 handles installing it.
        return True
    return current_mode() in SUFFICIENT_MODES


def up() -> bool:
    if not _cmux_present():
        print("  cmux not installed — skipping (see migration 097)")
        return True

    changed, status = ensure()
    print(f"  {status}")

    if not changed and current_mode() not in SUFFICIENT_MODES:
        print(f"  Could not update {CONFIG_FILE}")
        print('  Set automation.socketControlMode to "automation" by hand,')
        print("  then run: cmux reload-config")
        return False

    if changed:
        print("  `aos start` can now open a cmux workspace directly.")
    return True


def verify() -> bool:
    if not _cmux_present():
        return True
    return current_mode() in SUFFICIENT_MODES


if __name__ == "__main__":
    ok = up()
    sys.exit(0 if ok and verify() else 1)
