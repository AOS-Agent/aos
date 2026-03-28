"""
Invariant: LaunchAgent plists reference Python binaries that actually exist.

Historical issue: Homebrew upgrades can change Python paths
(python3.12 → python3.13), leaving plists pointing at a binary that
no longer exists. Services silently fail to start.
"""

import re
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class LaunchAgentPythonCheck(ReconcileCheck):
    name = "launchagent_python_paths"
    description = "LaunchAgent plists reference Python that exists on disk"

    LA_DIR = Path.home() / "Library" / "LaunchAgents"
    AOS_PLIST_PREFIX = "com.aos."

    # Candidate Python binaries in preference order
    PYTHON_CANDIDATES = [
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
    ]

    def check(self) -> bool:
        if not self.LA_DIR.exists():
            return True  # No LaunchAgents dir = nothing to check

        for plist in self.LA_DIR.glob(f"{self.AOS_PLIST_PREFIX}*.plist"):
            text = plist.read_text()
            for python_path in self._find_python_refs(text):
                if not Path(python_path).exists():
                    return False
        return True

    def fix(self) -> CheckResult:
        current_python = self._find_current_python()
        if not current_python:
            return CheckResult(
                self.name, Status.NOTIFY,
                "LaunchAgent Python paths are stale but no valid Python found",
                detail="Checked: " + ", ".join(self.PYTHON_CANDIDATES),
                notify=True,
            )

        fixed = []
        for plist in self.LA_DIR.glob(f"{self.AOS_PLIST_PREFIX}*.plist"):
            text = plist.read_text()
            stale_refs = [p for p in self._find_python_refs(text)
                         if not Path(p).exists()]
            if not stale_refs:
                continue

            new_text = text
            for stale in stale_refs:
                new_text = new_text.replace(stale, str(current_python))

            if new_text != text:
                # Unload, fix, reload
                subprocess.run(
                    ["launchctl", "unload", str(plist)],
                    capture_output=True, timeout=10,
                )
                plist.write_text(new_text)
                subprocess.run(
                    ["launchctl", "load", str(plist)],
                    capture_output=True, timeout=10,
                )
                fixed.append(plist.name)

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Updated Python paths in: {', '.join(fixed)} → {current_python}"
            )
        return CheckResult(self.name, Status.OK, "ok")

    def _find_python_refs(self, text: str) -> list[str]:
        """Extract Python binary paths from plist XML."""
        return re.findall(r'/[\w/.-]+python3(?:\.\d+)?', text)

    def _find_current_python(self) -> Path | None:
        """Find the best available Python 3.11+."""
        for candidate in self.PYTHON_CANDIDATES:
            p = Path(candidate)
            if p.exists():
                return p
        return None
