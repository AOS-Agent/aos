"""
Invariant: The transcriber service is running and healthy on port 7601.

If the venv exists but the service isn't responding, restart it.
If the venv doesn't exist, notify — migration 019 needs to run.
"""

import json
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class TranscriberServiceCheck(ReconcileCheck):
    name = "transcriber_service"
    description = "Transcriber service is running and healthy on port 7601"

    VENV_PYTHON = Path.home() / ".aos" / "services" / "transcriber" / ".venv" / "bin" / "python"
    SERVICE_MAIN = Path.home() / "aos" / "core" / "services" / "transcriber" / "main.py"
    HEALTH_URL = "http://127.0.0.1:7601/health"
    PLIST_NAME = "com.aos.transcriber"

    def check(self) -> bool:
        # No venv = migration hasn't run yet, skip
        if not self.VENV_PYTHON.exists():
            return True

        try:
            req = Request(self.HEALTH_URL, method="GET")
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("status") in ("ready", "loading")
        except Exception:
            return False

    def fix(self) -> CheckResult:
        if not self.VENV_PYTHON.exists():
            return CheckResult(
                self.name, Status.SKIP,
                "Transcriber venv not found — run migration 019 first"
            )

        if not self.SERVICE_MAIN.exists():
            return CheckResult(
                self.name, Status.NOTIFY,
                "Transcriber service code not found at expected path",
                detail=str(self.SERVICE_MAIN),
                notify=True,
            )

        # Try to restart via launchctl
        plist = Path.home() / "Library" / "LaunchAgents" / f"{self.PLIST_NAME}.plist"
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, timeout=10,
            )
            result = subprocess.run(
                ["launchctl", "load", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return CheckResult(
                    self.name, Status.FIXED,
                    "Restarted transcriber service via launchctl"
                )

        return CheckResult(
            self.name, Status.NOTIFY,
            "Transcriber service is down and couldn't be restarted",
            detail="Check logs at ~/.aos/logs/transcriber.err.log",
            notify=True,
        )
