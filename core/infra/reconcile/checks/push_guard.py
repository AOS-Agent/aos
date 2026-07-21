"""Reconcile check: the dev workspace must have the pre-push guard installed.

Invariant: ~/project/aos/.git/hooks/pre-push is a symlink to (or copy of)
core/bin/internal/pre-push-guard, so every session pushing from the dev
workspace passes the aos#192 gate: no personal-data leaks, no oversized
binaries, no stale-tree clobbers that delete shipped migrations/tests.

Why (2026-07-21): two sibling sessions pushed ungated in one day — one
reverted the shipped kanban Phase 0 (stale worktree snapshot), another
landed personal data + a 28MB binary. Gates that only live in agent
briefs are advisory; this one lives in git itself.
"""

from pathlib import Path

from base import CheckResult, ReconcileCheck, Status

DEV_REPO = Path.home() / "project" / "aos"
HOOK_DST = DEV_REPO / ".git" / "hooks" / "pre-push"
HOOK_SRC = DEV_REPO / "core" / "bin" / "internal" / "pre-push-guard"


class PushGuardCheck(ReconcileCheck):
    name = "push_guard"
    description = "Dev workspace pre-push hook installed (aos#192)"

    def check(self) -> bool:
        # Developer-role machines only; a machine without the dev
        # workspace has nothing to guard.
        if not DEV_REPO.exists():
            return True
        if not HOOK_SRC.exists():
            # Source not yet pulled — nothing to install from; not a failure
            # of this machine's state.
            return True
        if not HOOK_DST.exists():
            return False
        try:
            if HOOK_DST.is_symlink():
                return HOOK_DST.resolve() == HOOK_SRC.resolve()
            # Copy installs are acceptable if content matches.
            return HOOK_DST.read_bytes() == HOOK_SRC.read_bytes()
        except OSError:
            return False

    def fix(self) -> CheckResult:
        try:
            HOOK_DST.parent.mkdir(parents=True, exist_ok=True)
            if HOOK_DST.exists() or HOOK_DST.is_symlink():
                HOOK_DST.unlink()
            HOOK_DST.symlink_to(HOOK_SRC)
            return CheckResult(
                self.name, Status.FIXED,
                "Installed the pre-push guard in the dev workspace",
            )
        except OSError as e:
            return CheckResult(
                self.name, Status.NOTIFY,
                "Couldn't install the push guard in the dev workspace",
                detail=f"symlink failed: {e}",
                notify=True,
            )
