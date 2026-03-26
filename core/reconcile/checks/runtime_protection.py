"""
Invariant: ~/aos/ (runtime copy) is clean and protected from local commits.

Three protections:
1. A pre-commit hook that blocks ALL commits to ~/aos/
2. A .no-auto-commit marker file
3. If the repo is dirty or has local commits ahead of origin, auto-clean

Why: Services or sessions sometimes write files into ~/aos/ by accident.
If those files get committed, `git pull --ff-only` fails and the machine
can't update. This has happened with .inflight.json, .jsonl transcripts,
and execution logs.

See GitHub issue #9.
"""

import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


RUNTIME_DIR = Path.home() / "aos"

PRE_COMMIT_HOOK = """\
#!/bin/bash
# Installed by AOS reconcile — do not remove
# ~/aos/ is a read-only runtime copy. All commits go to ~/project/aos/
echo "ERROR: ~/aos/ is read-only runtime. Commit to ~/project/aos/ instead." >&2
exit 1
"""


class RuntimeProtectionCheck(ReconcileCheck):
    name = "runtime_protection"
    description = "~/aos/ is clean and protected from accidental commits"

    hooks_dir = RUNTIME_DIR / ".git" / "hooks"
    hook_path = RUNTIME_DIR / ".git" / "hooks" / "pre-commit"
    marker_path = RUNTIME_DIR / ".no-auto-commit"

    def check(self) -> bool:
        if not RUNTIME_DIR.is_dir():
            return True  # No runtime dir, nothing to protect
        if not (RUNTIME_DIR / ".git").is_dir():
            return True  # Not a git repo

        # Check 1: pre-commit hook exists and blocks commits
        if not self.hook_path.exists():
            return False
        if "read-only runtime" not in self.hook_path.read_text():
            return False

        # Check 2: .no-auto-commit marker exists
        if not self.marker_path.exists():
            return False

        # Check 3: repo is clean (no uncommitted changes)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return False

        # Check 4: no local commits ahead of origin/main
        result = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=10
        )
        try:
            ahead = int(result.stdout.strip())
            if ahead > 0:
                return False
        except (ValueError, AttributeError):
            pass  # Can't determine — skip this check

        return True

    def fix(self) -> CheckResult:
        if not RUNTIME_DIR.is_dir() or not (RUNTIME_DIR / ".git").is_dir():
            return CheckResult(self.name, Status.SKIP, "~/aos/ not a git repo")

        fixed = []

        # Fix 1: Install pre-commit hook
        if not self.hook_path.exists() or "read-only runtime" not in self.hook_path.read_text():
            self.hooks_dir.mkdir(parents=True, exist_ok=True)
            self.hook_path.write_text(PRE_COMMIT_HOOK)
            self.hook_path.chmod(0o755)
            fixed.append("pre-commit hook")

        # Fix 2: Create .no-auto-commit marker
        if not self.marker_path.exists():
            self.marker_path.touch()
            fixed.append(".no-auto-commit marker")

        # Fix 3: Clean dirty working tree
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            # Reset tracked files, clean untracked (but not ignored)
            subprocess.run(
                ["git", "checkout", "--", "."],
                cwd=RUNTIME_DIR, capture_output=True, timeout=10
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=RUNTIME_DIR, capture_output=True, timeout=10
            )
            fixed.append("cleaned dirty files")

        # Fix 4: Reset local commits ahead of origin
        result = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=10
        )
        try:
            ahead = int(result.stdout.strip())
            if ahead > 0:
                subprocess.run(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=RUNTIME_DIR, capture_output=True, timeout=10
                )
                fixed.append(f"reset {ahead} local commits to origin/main")
        except (ValueError, AttributeError):
            pass

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Runtime protection: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
