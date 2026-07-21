"""Import the legacy islah ``bugs.yaml`` ledger into ``work.db``.

Two callers, one idempotent path (``bug_tasks.file_bug`` keyed on
``islah:<id>``):

  * the **one-shot migration** (091) runs this once at deploy against the real
    ``~/.aos/islah/bugs.yaml`` — the 19 bugs become ``pipeline='bug'`` tasks.
  * the **mirror cron** runs it on a schedule while the operator's islah CLI may
    still append to ``bugs.yaml`` before Phase-7 cutover: new ledger rows mirror
    into ``work.db``; ``work.db`` never writes back (dossier risk-1, one-way).

Each bug's lifecycle is reconstructed as a faithful activity narrative —
reported → triaged → attempts[] → proof[] → current status — with the ORIGINAL
timestamps preserved in every beat's ``ts``. The islah 13-state machine maps
1:1 onto the bug pipeline stages (``pipelines.BUG_STAGES``), which is why islah's
model was chosen as the framework bug pipeline in Phase 1.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from core.engine.work.apps_registry import get_app
from core.engine.work.intake.bug_tasks import file_bug, status_beat
from core.engine.work.pipelines import BUG_STAGE_INTAKE, is_bug_stage

DEFAULT_BUGS_YAML = Path.home() / ".aos" / "islah" / "bugs.yaml"

# The bug fields (from the islah Bug record) carried verbatim into fields JSON.
_CARRY_FIELDS = (
    "severity", "classification", "build", "screen", "symptom",
    "root_cause", "fix_approach", "conflict", "repo", "branch",
    "build_status", "kind", "reporter", "source_text",
)


def _clip(s, n: int = 100) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _stage_for(status: str | None) -> str:
    """The bug-pipeline stage an islah status maps to (they are 1:1)."""
    return status if is_bug_stage(status) else BUG_STAGE_INTAKE


def _fields_for(bug: dict) -> dict:
    out: dict = {"islah_id": bug.get("id"), "islah_status": bug.get("status"), "app": bug.get("app")}
    for k in _CARRY_FIELDS:
        v = bug.get(k)
        if v not in (None, "", [], 0):
            out[k] = v
    if bug.get("code_refs"):
        out["code_refs"] = list(bug["code_refs"])
    if bug.get("commits"):
        # Keep the commit richness (sha/subject/files) without the board caring.
        out["commits"] = bug["commits"]
    return out


def _activities_for(bug: dict) -> list[dict]:
    """Reconstruct the ordered lifecycle beats after the created beat."""
    beats: list[dict] = []
    attempts = bug.get("attempts") or []
    proofs = bug.get("proof") or []
    updated = bug.get("updated") or bug.get("created")
    first_attempt_ts = attempts[0].get("at") if attempts else None

    # triaged — the investigation that wrote root_cause / code_refs / fix_approach.
    if bug.get("root_cause"):
        beats.append({
            "kind": "attempt",
            "body": f"Triaged — {_clip(bug['root_cause'])}",
            "data": {
                "root_cause": bug.get("root_cause"),
                "code_refs": list(bug.get("code_refs") or []),
                "fix_approach": bug.get("fix_approach"),
                "conflict": bug.get("conflict"),
                "severity": bug.get("severity"),
                "classification": bug.get("classification"),
            },
            "ts": first_attempt_ts or updated,
            "actor": "islah-triage",
            "marker": "triaged",
        })

    # attempts[] — each fix attempt, verbatim (append-only in islah, preserved here).
    for a in attempts:
        n = a.get("n")
        beats.append({
            "kind": "attempt",
            "body": f"Attempt {n} — {_clip(a.get('hypothesis'))}",
            "data": {
                "n": n,
                "hypothesis": a.get("hypothesis"),
                "sha": a.get("sha") or None,
                "gate_result": a.get("gate_result") or None,
                "at": a.get("at"),
            },
            "ts": a.get("at") or updated,
            "actor": "islah-fix",
            "marker": f"attempt:{n}",
        })

    # proof[] — before/after captures, verbatim.
    for i, p in enumerate(proofs):
        beats.append({
            "kind": "proof",
            "body": f"Proof ({p.get('kind', 'ref')}): {_clip(p.get('ref'))}",
            "data": {"kind": p.get("kind"), "ref": p.get("ref"), "at": p.get("at")},
            "ts": p.get("at") or updated,
            "actor": "islah-fix",
            "marker": f"proof:{i}",
        })

    # branch / commits — the linkage beat (Linear's magic-word made explicit).
    if bug.get("branch") or bug.get("commits"):
        commits = bug.get("commits") or []
        beats.append({
            "kind": "linked",
            "body": f"Branch {bug.get('branch') or '(detached)'}"
                    + (f" · {len(commits)} commit(s)" if commits else ""),
            "data": {
                "branch": bug.get("branch"),
                "commits": [c.get("sha") for c in commits if isinstance(c, dict)],
                "repo": bug.get("repo"),
            },
            "ts": updated,
            "actor": "islah-fix",
            "marker": "branch",
        })

    # current status — the coarse board move (skipped for a fresh 'new' bug).
    sb = status_beat(_stage_for(bug.get("status")), ts=updated, marker="status")
    if sb:
        sb["actor"] = "islah-import"
        beats.append(sb)

    return beats


def import_bugs(bugs_path: str | os.PathLike | None = None, engine=None) -> dict:
    """Import every bug from ``bugs_path`` into ``work.db``. Idempotent.

    Returns ``{total, created, skipped, activities, tasks:[...]}``. Passing an
    ``engine`` (the work backend module) is how tests bind to an isolated DB;
    the migration and cron leave it None to use the live backend.
    """
    if engine is None:
        import backend as engine  # the work backend, on sys.path via the caller

    path = Path(bugs_path) if bugs_path else DEFAULT_BUGS_YAML
    if not path.exists():
        return {"total": 0, "created": 0, "skipped": 0, "activities": 0,
                "tasks": [], "note": f"no ledger at {path}"}

    raw = yaml.safe_load(path.read_text()) or {}
    bugs = raw.get("bugs", []) or []

    created = skipped = activities = 0
    tasks: list[dict] = []
    for bug in bugs:
        bid = bug.get("id")
        if not bid:
            continue
        stage = _stage_for(bug.get("status"))
        app = bug.get("app")
        app_name = (get_app(app).name if get_app(app) else None) if app else None
        title = bug.get("title") or bug.get("symptom") or bid
        reported = bug.get("reported") or bug.get("created")

        res = file_bug(
            engine,
            title=title,
            app=app,
            source="islah-import",
            source_ref=f"islah:{bid}",
            fields=_fields_for(bug),
            stage=stage,
            reported_ts=reported,
            activities=_activities_for(bug),
            created_body=f"Reported via {bug.get('source', 'islah')}: {_clip(bug.get('source_text') or title, 120)}",
            created_data={"reporter": bug.get("reporter"), "islah_id": bid,
                          "islah_status": bug.get("status"),
                          "severity": bug.get("severity")},
            actor="islah-import",
            project_name=app_name,
        )
        if res["created"]:
            created += 1
        else:
            skipped += 1
        activities += res["activities"]
        tasks.append({"islah_id": bid, "task_id": res["task_id"],
                      "stage": stage, "created": res["created"]})

    return {"total": len(bugs), "created": created, "skipped": skipped,
            "activities": activities, "tasks": tasks}
