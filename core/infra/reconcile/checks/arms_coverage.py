"""
Invariant: the module manifest describes every AOS service on this machine,
and nothing it describes is silently broken.

Two failures this check exists to prevent, both observed on 2026-08-17:

1. COVERAGE. modules.yaml described 16 modules while 25 AOS LaunchAgents were
   running — including qareen (:4096), which the spec calls core. A manifest
   that omits three-fifths of the system is not a source of truth, and the
   Arms panel rendered from it was confidently incomplete. Anything the
   manifest cannot name, it cannot report on, install, or remove.

2. SILENT DEGRADATION. Two services were running, green in launchctl, green in
   every dashboard — and sitting on a Python interpreter that could no longer
   install a package. The bridge lost its :4098 API that way and nothing said
   a word for days. `degraded` is the state that catches this, and it is worth
   nothing if no scheduled job ever looks.

This check never auto-fixes. Adopting a service into the manifest is a
judgement about what the thing IS (daemon? periodic? core or experimental?)
and a wrong guess produces exactly the false BROKEN reports that motivated
schema 2. It reports and notifies; a human writes the entry.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status, aos_installed

# checks/ -> reconcile/ -> infra/ -> core/  … then core/engine holds the arm engine.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine"))


def _load():
    from arms.manifest import load_manifest
    from arms.probe import probe_all, unmanaged_labels
    manifest = load_manifest()
    return manifest, probe_all(manifest), unmanaged_labels(manifest)


class ArmsCoverageCheck(ReconcileCheck):
    name = "arms_coverage"
    description = "modules.yaml covers every AOS service, and none are broken or degraded"

    def precondition(self) -> bool:
        if not aos_installed():
            return False
        # No manifest at all means the arm engine has not shipped here yet;
        # that is not a violated invariant, it is nothing to check.
        return any(
            (Path.home() / p).exists()
            for p in ("aos/config/modules.yaml", "project/aos/config/modules.yaml")
        )

    def check(self) -> bool:
        try:
            _manifest, states, stray = _load()
        except Exception:
            # A manifest that cannot be parsed IS a violation — fix() reports why.
            return False
        if stray:
            return False
        return not any(s.status in ("broken", "degraded") for s in states)

    def fix(self) -> CheckResult:
        try:
            _manifest, states, stray = _load()
        except Exception as exc:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="modules.yaml could not be loaded",
                detail=f"{type(exc).__name__}: {exc}",
                notify=True,
            )

        broken = [s for s in states if s.status == "broken"]
        degraded = [s for s in states if s.status == "degraded"]

        lines: list[str] = []
        if stray:
            lines.append(
                f"{len(stray)} AOS service(s) on this machine that no module describes:"
            )
            lines += [f"  · {label}" for label in stray]
            lines.append(
                "  Adopt each into config/modules.yaml (with tier + kind), or "
                "declare it under `foreign:` if AOS does not own it."
            )
        for s in broken:
            lines.append(f"BROKEN   {s.id} — {s.why or 'not running'}")
        for s in degraded:
            lines.append(f"degraded {s.id} — {s.why}")

        summary_parts = []
        if stray:
            summary_parts.append(f"{len(stray)} unmanaged")
        if broken:
            summary_parts.append(f"{len(broken)} broken")
        if degraded:
            summary_parts.append(f"{len(degraded)} degraded")
        summary = ", ".join(summary_parts) or "manifest drift"

        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message=f"Arms: {summary}",
            detail="\n".join(lines),
            notify=True,
        )
