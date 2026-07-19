"""
Invariant: every ACTIVE resident service is loaded (and, where its declared
liveness is http, responding); no RETIRED service is loaded.

This is the exact silent state that ate the bridge and the transcriber during
the v0.6.10/0.6.11 update cycles (aos#180): the plist was intact on disk, the
venv was intact, but a raced bootout left the launchd job UNLOADED — and
`launchctl list`-style process checks that already assume a loaded job could
not see it. KeepAlive cannot recover an *unloaded* job, so it stayed dead until
a human re-bootstrapped it.

Every decision here derives from the service registry (one service.yaml per
service — core/infra/lib/service_registry.py), never from a hardcoded list or a
guessed plist shape:

  * active resident, liveness http     → must be loaded AND healthy
  * active resident, poll/keepalive    → must be loaded (its own check owns the
    or type interval                     wedge/interval semantics; loaded-is-enough)
  * optional                           → if loaded, held to the same health bar;
                                         if absent, that is fine (never restarted)
  * retired                            → must NOT be loaded; if it still is, we
                                         NOTIFY the operator (we never auto-bootout
                                         — removing a service is an operator call)
  * a deployed plist with no manifest  → loaded-check only (external tools like
    (e.g. sentinel, headscale)           sentinel/headscale keep prior behavior)

Registered with periodic_fix=True, so it is the ONE service check allowed to
repair on the lightweight periodic reconcile — a dead service shouldn't wait for
the next deploy. All restarts route through the shared guarded choke-point
(service_ctl), which records them, so this check reads the lifecycle log to
avoid re-restarting a service another check just restarted (anti-flap).
"""

import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.service_ctl import (
    is_loaded,
    last_restart_age,
    restart_launchagent,
)
from lib.service_registry import ManifestError, load_registry

# Liveness strategies for which "the job is loaded" is the whole signal — the
# service's own dedicated check (or launchd KeepAlive) owns anything finer.
_LOADED_IS_ENOUGH = {"poll_timestamp", "keepalive", "interval", "none"}


class ServiceLoadedCheck(ReconcileCheck):
    name = "service_loaded"
    description = "Active resident services are loaded/healthy; retired services are not loaded"

    # Allowed to repair on the periodic reconcile, not just on deploys — the
    # unloaded-service state is exactly what shouldn't wait for a release.
    periodic_fix = True

    LA_DIR = Path.home() / "Library" / "LaunchAgents"
    PLIST_GLOB = "com.aos.*.plist"

    # A health-triggered restart is skipped if the service was restarted (by any
    # check, via the shared choke-point) within this window — prevents flapping
    # with the per-service checks on a deploy run and with itself across cycles.
    # An *unloaded* active service is always restarted regardless (it is the
    # critical silent-death state; there is nothing to flap against).
    RESTART_COOLDOWN_S = 180

    HEALTH_TIMEOUT_S = 5

    # ── Discovery ──────────────────────────────────────────────────────────

    def _plists(self) -> list[Path]:
        """Deployed AOS LaunchAgent plists. com.aos.* covers all but whatsmeow,
        which the registry declares under com.agent.* — include that label's
        plist explicitly if present."""
        if not self.LA_DIR.exists():
            return []
        plists = set(self.LA_DIR.glob(self.PLIST_GLOB))
        try:
            reg = load_registry()
        except ManifestError:
            reg = None
        if reg is not None:
            for m in reg:
                if not m.label.startswith("com.aos."):
                    p = self.LA_DIR / f"{m.label}.plist"
                    if p.exists():
                        plists.add(p)
        return sorted(plists)

    def _is_healthy(self, url: str) -> bool:
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=self.HEALTH_TIMEOUT_S) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _evaluate(self) -> list[dict]:
        """Return one record per deployed plist. Fields:
        {label, plist, svc, loaded, health_url, broken, reason, retired_loaded}."""
        try:
            reg = load_registry()
        except ManifestError:
            reg = None

        records = []
        for plist in self._plists():
            label = plist.stem
            manifest = reg.by_label(label) if reg is not None else None
            svc = manifest.name if manifest else label.removeprefix("com.aos.").removeprefix("com.agent.")
            loaded = is_loaded(label)

            broken = False
            reason = None
            retired_loaded = False
            health_url = None

            if manifest is not None and manifest.status == "retired":
                # Must NOT be loaded. If it is, flag for the operator — but never
                # auto-bootout (removing a service is an operator decision).
                if loaded:
                    retired_loaded, reason = True, "retired service still loaded"
            elif manifest is not None:
                optional = manifest.status == "optional"
                loaded_is_enough = (
                    manifest.type == "interval"
                    or manifest.liveness in _LOADED_IS_ENOUGH
                )
                health_url = None if loaded_is_enough else manifest.health_url
                if not loaded:
                    # An optional service may legitimately be absent on this node.
                    if not optional:
                        broken, reason = True, "not loaded"
                elif health_url is not None:
                    if not self._is_healthy(health_url):
                        broken, reason = True, "health endpoint not responding"
            else:
                # No manifest: external plist (sentinel, headscale, …). Preserve
                # prior behavior — assert the job is loaded. (launchctl reports an
                # interval job as loaded between ticks, so "not loaded" here means
                # genuinely unregistered, not merely idle.)
                if not loaded:
                    broken, reason = True, "not loaded"

            records.append({
                "label": label, "plist": plist, "svc": svc, "loaded": loaded,
                "health_url": health_url, "broken": broken, "reason": reason,
                "retired_loaded": retired_loaded,
            })
        return records

    # ── Check / fix ────────────────────────────────────────────────────────

    def check(self) -> bool:
        return not any(r["broken"] or r["retired_loaded"] for r in self._evaluate())

    def fix(self) -> CheckResult:
        records = self._evaluate()
        broken = [r for r in records if r["broken"]]
        retired = [r for r in records if r["retired_loaded"]]
        if not broken and not retired:
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
        if retired:
            # Never auto-bootout — surface for the operator to unload.
            parts.append("RETIRED but still loaded (operator should unload): "
                         + ", ".join(r["svc"] for r in retired))
        message = "; ".join(parts) if parts else "no action"

        # Anything that failed to reload, a retired service still loaded, or
        # nothing fixable (all skipped) needs operator attention. A pure restart
        # is a clean FIXED.
        if failed or retired:
            return CheckResult(self.name, Status.NOTIFY, message, notify=True)
        if not fixed and skipped:
            return CheckResult(self.name, Status.NOTIFY, message)
        return CheckResult(self.name, Status.FIXED, message)
