"""
Invariant: Runtime logs are in ~/.aos/logs/, not inside ~/aos/ (git-tracked).

Historical issue: execution_log/ was created inside ~/aos/ and auto-committed,
polluting the repo history with runtime data.
"""

import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class LogLocationCheck(ReconcileCheck):
    name = "execution_log_location"
    description = "execution_log/ is in ~/.aos/logs/, not ~/aos/"

    WRONG = Path.home() / "aos" / "execution_log"
    RIGHT = Path.home() / ".aos" / "logs" / "execution"

    def check(self) -> bool:
        return not self.WRONG.exists()

    def fix(self) -> CheckResult:
        self.RIGHT.mkdir(parents=True, exist_ok=True)

        moved = 0
        for f in self.WRONG.iterdir():
            if f.is_file():
                dest = self.RIGHT / f.name
                if not dest.exists():
                    f.rename(dest)
                    moved += 1
                else:
                    # Both exist — append, don't overwrite
                    with open(dest, "a") as out, open(f) as inp:
                        out.write(inp.read())
                    f.unlink()
                    moved += 1

        # Remove the directory from the git-tracked area
        if self.WRONG.exists():
            shutil.rmtree(self.WRONG)

        return CheckResult(
            self.name, Status.FIXED,
            f"Moved {moved} log file(s) from ~/aos/execution_log/ to ~/.aos/logs/execution/"
        )
