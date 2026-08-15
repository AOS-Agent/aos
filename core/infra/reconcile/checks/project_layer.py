"""
Invariant: every directory under ~/project/ is accounted for and still is what
it claims to be.

Two different questions, and this check only asks the second one. Disposition —
"is this directory accounted for" — is a triage question with a human answer,
and `work projects reconcile` exists for the operator to sit down with. Drift —
"does how this directory sits still match what it claims to be" — is a standing
condition, and standing conditions are what reconcile checks are for.

Eight drift kinds, all sourced from project_reconcile's typed Drift rows:

    unmanifested              a directory that cannot identify itself
    manifest_invalid          it tried to, and the manifest does not validate.
                              Distinct from unmanifested because the fix is
                              different: `project adopt` is the answer to a
                              missing manifest and would regenerate over a
                              broken one, so this kind never advises it.
    layer_not_installed       a zone or ~/project/CLAUDE.md is missing. Migration
                              102 defers zone creation when ~/project cannot be
                              written into (an unmounted volume), and without
                              this nothing would ever say the install is half
                              done — every verb lazily creates what it needs.
    archived_but_active       marked finished, still being worked in
    archived_not_moved        marked finished, still at the top level: the
                              flip-in-place fallback, whose blockers were never
                              cleared
    no_git_unmarked           unversioned, with nothing recording that decision
    third_party_at_top_level  someone else's repo among the operator's work
    dirty_tree                work that exists nowhere but this disk

REPORTS, NEVER CORRECTS. This is the council's lock, not a limitation: the
corrections available here are `git init`, `git commit` and moving directories,
and a health check that does any of those unattended is a health check that can
lose work. `project new` / `adopt` / `archive` are the sanctioned mutations, and
they are typed by a human.

The reconciler runs in drift_only mode. The full pass costs ~19s, almost all of
it grepping 22.5k-commit repos for hardcoded paths to resolve component
relationships — worth it for a triage session, wrong for something that runs
every half hour. drift_only answers the same five questions in ~3s.

Fresh installs: no ~/project/ at all is the expected state until the first
`project new` lazy-creates the zones, and this check SKIPs there rather than
reporting OK. The tempting alternative — "no directories, therefore no misfiled
directory, therefore healthy" — is true and still wrong to report, because it is
indistinguishable from a check that looked at nothing. ~/project/ is an input
this check READS, not the condition it TESTS, which is exactly the line
base.py draws for precondition(); a check for whether ~/project/ ought to exist
would be a different check. See tests/test_reconcile_blindness.py, the ratchet
this invariant is worth.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status, aos_installed

# The work engine's modules are flat, not a package. Resolved from this file
# rather than from ~/aos so the check is importable in a dev worktree too.
_WORK_DIR = Path(__file__).resolve().parents[3] / "engine" / "work"
if str(_WORK_DIR) not in sys.path:
    sys.path.insert(0, str(_WORK_DIR))

# How many example directories to name per drift kind before summarising. A
# finding the operator cannot act on is noise, and so is thirty-one identical
# lines — naming a few and counting the rest keeps both halves.
SAMPLE = 3


class ProjectLayerCheck(ReconcileCheck):
    name = "project_layer"
    description = "~/project/ directories match what they claim to be"

    PROJECT_ROOT = Path.home() / "project"

    def __init__(self) -> None:
        # One reconcile pass per run, shared between check() and fix(). The
        # runner instantiates each check once, so caching here is safe and
        # halves the cost of a check that is already the slowest in the suite.
        self._report = None
        self._failed = None

    # ── the pass ────────────────────────────────────────────────────

    def _reconcile(self):
        """Run the reconciler once, in drift_only mode. None if it cannot run."""
        if self._report is not None or self._failed:
            return self._report
        try:
            import project_reconcile
            self._report = project_reconcile.reconcile(drift_only=True)
        except Exception as e:
            self._failed = str(e)
            return None
        return self._report

    def _drift_by_kind(self) -> dict:
        r = self._reconcile()
        if r is None:
            return {}
        out: dict[str, list] = {}
        for d in r.drift:
            out.setdefault(d.kind, []).append(d)
        return out

    # ── the check ───────────────────────────────────────────────────

    def precondition(self) -> bool:
        """An AOS install, and a ~/project/ to have an opinion about.

        Without the directory there is no invariant to verify, and answering
        "fine" would be reporting on a machine this never looked at. The runner
        records SKIP — visible as unverified — until the first `project new`
        creates the structure.
        """
        return aos_installed() and self.PROJECT_ROOT.exists()

    def check(self) -> bool:
        """True when nothing under ~/project/ is drifting."""
        if not self.PROJECT_ROOT.exists():
            return True     # unreachable via the runner; precondition gates it
        r = self._reconcile()
        if r is None:
            return False        # could not evaluate → fix() reports why
        return not r.drift

    def fix(self) -> CheckResult:
        """Report the drift. Corrects nothing — see the module docstring."""
        if not self.PROJECT_ROOT.exists():
            return CheckResult(
                self.name, Status.OK,
                "no ~/project/ yet — the zones are created by the first "
                "`project new`")

        if self._reconcile() is None:
            return CheckResult(
                self.name, Status.ERROR,
                f"project reconciler could not run: {self._failed}",
                detail="Drift under ~/project/ is unverified until this is fixed.",
                notify=True,
            )

        by_kind = self._drift_by_kind()
        if not by_kind:
            return CheckResult(self.name, Status.OK,
                               "every directory under ~/project/ is accounted for")

        summary, detail = [], []
        for kind, rows in sorted(by_kind.items(),
                                 key=lambda kv: (-len(kv[1]), kv[0])):
            summary.append(f"{kind}={len(rows)}")
            named = ", ".join(d.name for d in rows[:SAMPLE])
            more = f" (+{len(rows) - SAMPLE} more)" if len(rows) > SAMPLE else ""
            detail.append(f"{kind}: {named}{more} — {rows[0].evidence}")

        total = sum(len(v) for v in by_kind.values())
        return CheckResult(
            self.name, Status.NOTIFY,
            f"{total} drift finding(s) under ~/project/: {', '.join(summary)}. "
            f"Run `project list` to see them, `project adopt`/`archive` to fix.",
            detail="; ".join(detail),
            # Log-only, like DeadCodeCheck. This is a standing condition that
            # clears through a deliberate triage session, not an incident — and
            # the counts shift as ordinary work makes trees dirty, so a
            # notifying version would re-ping on every fluctuation and train the
            # operator to ignore it.
            notify=False,
        )
