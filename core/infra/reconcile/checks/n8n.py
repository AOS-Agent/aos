"""
Invariant: The n8n automation service is running and healthy on its declared port.

Full lifecycle:
- If n8n data dir doesn't exist → skip (migration 056 hasn't run yet)
- If data dir exists but plist missing → instantiate from template, deploy, start
- If plist exists but service unhealthy → kickstart
- If healthy → OK

The health URL is read from the service registry (config/services.d/n8n.yaml),
never hardcoded here.
"""

import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.service_ctl import restart_launchagent
from lib.service_registry import ManifestError, load_registry


def _registry_health_url(name: str) -> str | None:
    try:
        m = load_registry().by_name(name)
        return m.health_url if m else None
    except ManifestError:
        return None


class N8nServiceCheck(ReconcileCheck):
    name = "n8n_service"
    description = "n8n automation service is running and healthy on its declared port"

    HOME = Path.home()
    N8N_DATA_DIR = HOME / ".aos" / "services" / "n8n"
    HEALTH_URL = _registry_health_url("n8n")
    PLIST_NAME = "com.aos.n8n"
    PLIST_PATH = HOME / "Library" / "LaunchAgents" / "com.aos.n8n.plist"
    TEMPLATE_PATH = HOME / "aos" / "config" / "launchagents" / "com.aos.n8n.plist.template"
    LOG_DIR = HOME / ".aos" / "logs"

    def _is_healthy(self) -> bool:
        """Check if the n8n health endpoint responds."""
        try:
            req = Request(self.HEALTH_URL, method="GET")
            with urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _deploy_plist(self) -> str | None:
        """Instantiate plist from template. Returns error message or None."""
        if not self.TEMPLATE_PATH.exists():
            return f"Template not found: {self.TEMPLATE_PATH}"

        template = self.TEMPLATE_PATH.read_text()
        plist_content = template.replace("__HOME__", str(self.HOME))

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.PLIST_PATH.write_text(plist_content)
        return None

    def _kickstart(self) -> bool:
        """Start or restart the service through the shared guarded choke-point
        (settle → verify → retry → kickstart, with lifecycle audit logging).
        Returns True iff the job is verified loaded afterwards.
        """
        return restart_launchagent(
            self.PLIST_NAME, self.PLIST_PATH, actor="reconcile:n8n"
        )

    def check(self) -> bool:
        # No data dir = migration hasn't run yet, skip
        if not self.N8N_DATA_DIR.exists():
            return True

        # Registry unavailable — can't derive the health URL, so don't flap.
        if not self.HEALTH_URL:
            return True

        # n8n binary must exist
        result = subprocess.run(
            ["which", "n8n"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False

        # Data dir exists — service should be running and healthy
        return self._is_healthy()

    def fix(self) -> CheckResult:
        if not self.N8N_DATA_DIR.exists():
            return CheckResult(
                self.name, Status.SKIP,
                "n8n data dir not found — run migration 056 first"
            )

        # Check n8n binary exists
        result = subprocess.run(
            ["which", "n8n"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                self.name, Status.NOTIFY,
                "n8n binary not found — install with: npm install -g n8n",
                notify=True,
            )

        fixed = []

        # Step 1: Deploy plist if missing
        if not self.PLIST_PATH.exists():
            error = self._deploy_plist()
            if error:
                return CheckResult(
                    self.name, Status.NOTIFY,
                    f"Cannot deploy n8n plist: {error}",
                    notify=True,
                )
            fixed.append("deployed plist from template")

        # Step 2: Kickstart the service
        if self._kickstart():
            fixed.append("kickstarted service")
        else:
            return CheckResult(
                self.name, Status.NOTIFY,
                "n8n plist deployed but kickstart failed",
                detail="Check logs at ~/.aos/logs/n8n.err.log",
                notify=True,
            )

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"n8n: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
