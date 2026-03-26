"""
Invariant: The transcriber service is running and healthy on port 7601.

Full lifecycle:
- If venv doesn't exist → skip (migration 019 hasn't run yet)
- If venv exists but plist missing → instantiate from template, deploy, start
- If plist exists but service unhealthy → kickstart (not unload/load — avoids throttling)
- If healthy → OK

See GitHub issue #8: plist template existed but was never instantiated.
"""

import json
import os
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

    HOME = Path.home()
    VENV_PYTHON = HOME / ".aos" / "services" / "transcriber" / ".venv" / "bin" / "python"
    SERVICE_MAIN = HOME / "aos" / "core" / "services" / "transcriber" / "main.py"
    HEALTH_URL = "http://127.0.0.1:7601/health"
    PLIST_NAME = "com.aos.transcriber"
    PLIST_PATH = HOME / "Library" / "LaunchAgents" / "com.aos.transcriber.plist"
    TEMPLATE_PATH = HOME / "aos" / "config" / "launchagents" / "com.aos.transcriber.plist.template"
    LOG_DIR = HOME / ".aos" / "logs"

    def _is_healthy(self) -> bool:
        """Check if the transcriber health endpoint responds."""
        try:
            req = Request(self.HEALTH_URL, method="GET")
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("status") in ("ready", "loading")
        except Exception:
            return False

    def _deploy_plist(self) -> str | None:
        """Instantiate plist from template. Returns error message or None."""
        if not self.TEMPLATE_PATH.exists():
            return f"Template not found: {self.TEMPLATE_PATH}"

        template = self.TEMPLATE_PATH.read_text()
        plist_content = template.replace("__HOME__", str(self.HOME))

        # Ensure log dir exists
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)

        self.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.PLIST_PATH.write_text(plist_content)
        return None

    def _kickstart(self) -> bool:
        """Start or restart the service using bootout/bootstrap/kickstart.
        This avoids launchd throttling issues that unload/load can't handle.
        """
        uid = os.getuid()
        domain_target = f"gui/{uid}"
        service_target = f"gui/{uid}/{self.PLIST_NAME}"

        # Bootout first (ignore failure — may not be registered)
        subprocess.run(
            ["launchctl", "bootout", service_target],
            capture_output=True, timeout=10,
        )

        # Bootstrap the plist
        result = subprocess.run(
            ["launchctl", "bootstrap", domain_target, str(self.PLIST_PATH)],
            capture_output=True, text=True, timeout=10,
        )

        # Kickstart to ensure it's actually running
        subprocess.run(
            ["launchctl", "kickstart", "-k", service_target],
            capture_output=True, timeout=10,
        )

        return result.returncode == 0

    def check(self) -> bool:
        # No venv = migration hasn't run yet, skip
        if not self.VENV_PYTHON.exists():
            return True

        # Venv exists — service should be running and healthy
        return self._is_healthy()

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

        fixed = []

        # Step 1: Deploy plist if missing
        if not self.PLIST_PATH.exists():
            error = self._deploy_plist()
            if error:
                return CheckResult(
                    self.name, Status.NOTIFY,
                    f"Cannot deploy transcriber plist: {error}",
                    notify=True,
                )
            fixed.append("deployed plist from template")

        # Step 2: Kickstart the service
        if self._kickstart():
            fixed.append("kickstarted service")
        else:
            return CheckResult(
                self.name, Status.NOTIFY,
                "Transcriber plist deployed but kickstart failed",
                detail="Check logs at ~/.aos/logs/transcriber.err.log",
                notify=True,
            )

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Transcriber: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
