"""
Invariant: The transcriber service is running and healthy on its declared port.

Full lifecycle:
- If venv doesn't exist → skip (migration 019 hasn't run yet)
- If venv exists but plist missing → instantiate from template, deploy, start
- If plist exists but service unhealthy → kickstart (not unload/load — avoids throttling)
- If healthy → OK

See GitHub issue #8: plist template existed but was never instantiated.

The health URL is read from the service registry (transcriber's service.yaml),
never hardcoded here. A stale local constant (:7601, which is whatsmeow) is what
made this check bounce a healthy transcriber on every deploy (aos#180); deriving
from the one manifest is what stops that drift class from recurring.
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.service_ctl import restart_launchagent
from lib.service_registry import ManifestError, load_registry


def _registry_health_url(name: str) -> str | None:
    """The health URL for ``name`` from the registry, or None if unavailable."""
    try:
        m = load_registry().by_name(name)
        return m.health_url if m else None
    except ManifestError:
        return None


class TranscriberServiceCheck(ReconcileCheck):
    name = "transcriber_service"
    description = "Transcriber service is running and healthy on its declared port"

    HOME = Path.home()
    VENV_PYTHON = HOME / ".aos" / "services" / "transcriber" / ".venv" / "bin" / "python"
    SERVICE_MAIN = HOME / "aos" / "core" / "services" / "transcriber" / "main.py"
    HEALTH_URL = _registry_health_url("transcriber")
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
        """Start or restart the service through the shared guarded choke-point
        (settle → verify → retry → kickstart, with lifecycle audit logging).
        Returns True iff the job is verified loaded afterwards.
        """
        return restart_launchagent(
            self.PLIST_NAME, self.PLIST_PATH, actor="reconcile:transcriber"
        )

    def check(self) -> bool:
        # No venv = migration hasn't run yet, skip
        if not self.VENV_PYTHON.exists():
            return True

        # Registry unavailable — can't derive the health URL, so don't flap.
        if not self.HEALTH_URL:
            return True

        # Venv exists — service should be running and healthy
        return self._is_healthy()

    def fix(self) -> CheckResult:
        if not self.VENV_PYTHON.exists():
            return CheckResult(
                self.name, Status.SKIP,
                "Transcriber venv not found — run migration 019 first"
            )

        if not self.HEALTH_URL:
            return CheckResult(
                self.name, Status.SKIP,
                "Transcriber health URL unavailable — service registry did not load"
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
