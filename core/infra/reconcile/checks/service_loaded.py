"""
Invariant: for every deployed com.aos.*.plist there is a launchd job actually
loaded (and, where a health URL is known, responding).

This is the exact silent state that ate the bridge and the transcriber during
the v0.6.10/0.6.11 update cycles (aos#180): the plist was intact on disk, the
venv was intact, but a raced bootout left the launchd job UNLOADED — and
`launchctl list`-style process checks that already assume a loaded job could
not see it. KeepAlive cannot recover an *unloaded* job, so it stayed dead until
a human re-bootstrapped it.

Unlike the per-service checks (bridge_poll_liveness, transcriber, n8n), which
each know one service's health protocol, this check is generic: it enumerates
the deployed plists (discovery, never a hardcoded list) and asserts each has a
loaded job. Because it is registered with periodic_fix=True, it is the ONE
service check allowed to repair on the lightweight periodic reconcile — a dead
service shouldn't wait for the next deploy.

By-design interval / one-shot jobs (scheduler, slack-watch: StartInterval and
no KeepAlive) are detected from their plist and treated as healthy whenever they
are *loaded* — "loaded but not currently running" is normal for them, so they
are never health-probed and never flapped.

All restarts route through the shared guarded choke-point (service_ctl), which
also records them, so this check reads the lifecycle log to avoid re-restarting
a service another check just restarted (anti-flap).
"""

import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.service_ctl import (
    KNOWN_HEALTH_URLS,
    is_loaded,
    last_restart_age,
    restart_launchagent,
)


class ServiceLoadedCheck(ReconcileCheck):
    name = "service_loaded"
    description = "Every deployed com.aos.*.plist has a loaded (and healthy) launchd job"

    # Allowed to repair on the periodic reconcile, not just on deploys — the
    # unloaded-service state is exactly what shouldn't wait for a release.
    periodic_fix = True

    LA_DIR = Path.home() / "Library" / "LaunchAgents"
    PLIST_GLOB = "com.aos.*.plist"

    # A health-triggered restart is skipped if the service was restarted (by any
    # check, via the shared choke-point) within this window — prevents flapping
    # with the per-service checks on a deploy run and with itself across cycles.
    # An *unloaded* service is always restarted regardless (it is the critical
    # silent-death state; there is nothing to flap against).
    RESTART_COOLDOWN_S = 180

    HEALTH_TIMEOUT_S = 5

    # ── Discovery ──────────────────────────────────────────────────────────

    def _plists(self) -> list[Path]:
        if not self.LA_DIR.exists():
            return []
        return sorted(self.LA_DIR.glob(self.PLIST_GLOB))

    @staticmethod
    def _svc_name(plist: Path) -> str:
        """com.aos.bridge.plist → bridge."""
        return plist.stem.removeprefix("com.aos.")

    @staticmethod
    def _is_interval_job(plist: Path) -> bool:
        """True for a by-design periodic/one-shot job: StartInterval present,
        KeepAlive absent (scheduler, slack-watch). Such a job is legitimately
        'loaded but not currently running', so it must not be health-probed or
        flapped. Read from the plist itself — never a hardcoded allowlist."""
        try:
            text = plist.read_text()
        except Exception:
            return False
        return "<key>StartInterval</key>" in text and "<key>KeepAlive</key>" not in text

    def _is_healthy(self, url: str) -> bool:
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=self.HEALTH_TIMEOUT_S) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _evaluate(self) -> list[dict]:
        """Return one record per deployed plist:
        {label, plist, svc, loaded, interval, health_url, healthy, broken, reason}."""
        records = []
        for plist in self._plists():
            label = plist.stem
            svc = self._svc_name(plist)
            interval = self._is_interval_job(plist)
            loaded = is_loaded(label)
            health_url = None if interval else KNOWN_HEALTH_URLS.get(svc)
            healthy = True
            reason = None
            broken = False

            if not loaded:
                broken, reason = True, "not loaded"
            elif health_url is not None:
                healthy = self._is_healthy(health_url)
                if not healthy:
                    broken, reason = True, "health endpoint not responding"

            records.append({
                "label": label, "plist": plist, "svc": svc, "loaded": loaded,
                "interval": interval, "health_url": health_url,
                "healthy": healthy, "broken": broken, "reason": reason,
            })
        return records

    # ── Check / fix ────────────────────────────────────────────────────────

    def check(self) -> bool:
        return not any(r["broken"] for r in self._evaluate())

    def fix(self) -> CheckResult:
        records = self._evaluate()
        broken = [r for r in records if r["broken"]]
        if not broken:
            return CheckResult(self.name, Status.OK, "ok")

        fixed, failed, skipped = [], [], []
        for r in broken:
            label = r["label"]
            # Anti-flap: only skip HEALTH-triggered restarts inside the cooldown.
            # An unloaded service is always restarted — that is the critical
            # silent-death state and there is nothing to flap against.
            if r["loaded"]:
                age = last_restart_age(label)
                if age is not None and age < self.RESTART_COOLDOWN_S:
                    skipped.append(f"{r['svc']} ({r['reason']}, restarted {int(age)}s ago — draining)")
                    continue

            if restart_launchagent(r["plist"].stem, r["plist"], actor="reconcile:service_loaded"):
                fixed.append(f"{r['svc']} ({r['reason']})")
            else:
                failed.append(f"{r['svc']} ({r['reason']})")

        parts = []
        if fixed:
            parts.append("restarted " + ", ".join(fixed))
        if skipped:
            parts.append("skipped (cooldown): " + ", ".join(skipped))
        if failed:
            parts.append("FAILED to reload: " + ", ".join(failed))
        message = "; ".join(parts) if parts else "no action"

        # If anything failed to reload, or nothing could be fixed (all skipped),
        # this needs operator attention. A pure restart is a clean FIXED.
        if failed:
            return CheckResult(self.name, Status.NOTIFY, message, notify=True)
        if not fixed and skipped:
            return CheckResult(self.name, Status.NOTIFY, message)
        return CheckResult(self.name, Status.FIXED, message)
