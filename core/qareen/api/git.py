"""Qareen API — Git/Ship cockpit routes.

The read-true, decision-persisting backend for a project's Git view. Two halves
on one surface, joined by stable commit SHAs:

  VISUALIZE  derived, ephemeral git state — branch, ahead/behind origin/main, the
             unmerged commit set (with %P parents for lane rendering), working-tree
             status. Computed by the bounded async runner, cached 5s on HEAD-sha.
  GUIDE      durable, operator-owned ship state — the branch's unmerged commits
             grouped into reviewable BATCHES, each with an audit STATUS and an
             operator DECISION, persisted per branch under ~/.aos/ship/.

INVARIANTS:
  * NO endpoint mutates git. NO network ops. The merge to main is never executed
    here — even when ready, v1 hands off a command plan for deliberate approval.
  * Unlinked / non-repo / missing projects degrade gracefully (404 / {linked:false}
    / {is_repo:false}) — never a 500.

Endpoints:
  GET  /api/git/{project_id}/status
  GET  /api/git/{project_id}/commits?base=origin/main&limit=60
  GET  /api/git/{project_id}/graph?base=origin/main&limit=6   (merged context below the line)
  GET  /api/git/{project_id}/worktrees
  GET  /api/git/{project_id}/batches?base=origin/main
  POST /api/git/{project_id}/batches/{batch_id}/decision   {decision, status?, note?}
"""

from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..git import runner, seed, store
from ..git.runner import GitTimeout, resolve_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["git"])

_VALID_DECISIONS = {"undecided", "ship", "defer", "hold"}
_VALID_STATUSES = {"built", "half-baked", "broken", "unknown"}

# A base ref is operator/network-supplied. Allow only real ref characters — no
# leading dash (would be parsed as a git option), no whitespace, no shell metachars.
# Covers branch/remote refs, @{upstream}, HEAD~3, refs/heads/x. Defense-in-depth on
# top of the runner's --end-of-options + --verify gate.
_BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}~^-]*$")
_NOTE_MAX = 2000  # cap operator note length written to the plan yaml


def _valid_base(base: str) -> bool:
    return bool(base) and len(base) <= 256 and _BASE_RE.match(base) is not None


def _bad_base() -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_base", "detail": "base must be a plain git ref"},
        status_code=422,
    )


class DecisionRequest(BaseModel):
    decision: str
    status: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timeout_payload(extra: dict | None = None) -> JSONResponse:
    body = {"linked": True, "is_repo": True, "error": "git_timeout"}
    if extra:
        body.update(extra)
    # 200 so the UI can render a graceful "git timed out" state rather than a hard error.
    return JSONResponse(body, status_code=200)


async def _seed_fresh_plan(project_id: str, repo, base_pref: str) -> dict:
    """Build a fresh plan from the spec + live unmerged set (not yet persisted)."""
    base, base_missing = await runner.resolve_base(repo, base_pref)
    if base is None:
        ordered: list[str] = []
        ahead = behind = 0
    else:
        ordered = await runner.ordered_unmerged_shas(repo, base)
        status = await runner.git_status(repo, base_pref)
        ahead, behind = status.get("ahead", 0), status.get("behind", 0)

    # Subjects only needed for the auto-group fallback (no spec). Cheap: reuse the
    # bounded commit list (capped) — auto-group on >200 commits stays approximate.
    subjects: dict[str, str] = {}
    if not seed.triage_path(_branch_of(repo)).exists() and ordered:
        commits = await runner.git_commits(repo, base_pref, limit=200)
        for c in commits.get("commits", []):
            subjects[c["sha"]] = c["subject"]

    head = await runner.head_sha(repo)
    return seed.build_plan(
        project=project_id,
        repo=str(repo),
        branch=_branch_of(repo),
        base=base or base_pref,
        ordered_shas=ordered,
        ahead=ahead,
        behind=behind,
        head=head,
        subjects=subjects,
    )


def _branch_of(repo) -> str:
    # The plan filename needs the branch. Endpoints fill this memo from the cached
    # status right after resolving the repo, so it is populated before any seed.
    return _BRANCH_CACHE.get(str(repo), "HEAD")


# Tiny per-process branch memo, filled by endpoints right after status resolves.
_BRANCH_CACHE: dict[str, str] = {}


def _public_plan(
    plan: dict, total_unmerged: int, subjects: dict | None = None
) -> dict:
    """Shape the plan for the API (batches + provenance + subject map).

    ``subjects`` is a sha → subject map for the whole unmerged set so the ledger
    can show every batch commit's message, not just those in the graph window.
    """
    return {
        "batches": plan.get("batches", []),
        "source": plan.get("source", "spec"),
        "status": plan.get("status", "seeded"),
        "drift": plan.get("drift", False),
        "overflow": plan.get("overflow", False),
        "gates": plan.get("gates", {}),
        "seed": plan.get("seed", {}),
        "subjects": subjects or {},
        "total_unmerged": total_unmerged,
        "total": len(plan.get("batches", [])),
        "base": plan.get("base"),
    }


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/git/{project_id}/status")
async def git_status_endpoint(
    request: Request,
    project_id: str = PathParam(...),
    base: str = Query("origin/main"),
):
    if not _valid_base(base):
        return _bad_base()
    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    try:
        status = await runner.git_status(res.repo, base)
    except GitTimeout:
        return _timeout_payload()
    _BRANCH_CACHE[str(res.repo)] = status.get("branch", "HEAD")
    return JSONResponse(status)


# ---------------------------------------------------------------------------
# GET /commits
# ---------------------------------------------------------------------------


@router.get("/git/{project_id}/commits")
async def git_commits_endpoint(
    request: Request,
    project_id: str = PathParam(...),
    base: str = Query("origin/main"),
    limit: int = Query(60, ge=1, le=200),
):
    if not _valid_base(base):
        return _bad_base()
    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    try:
        data = await runner.git_commits(res.repo, base, limit)
    except GitTimeout:
        return _timeout_payload({"commits": [], "total": 0, "truncated": False})
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# GET /graph  (bounded merged context below the ship line)
# ---------------------------------------------------------------------------


@router.get("/git/{project_id}/graph")
async def git_graph_endpoint(
    request: Request,
    project_id: str = PathParam(...),
    base: str = Query("origin/main"),
    limit: int = Query(6, ge=1, le=30),
):
    """The few merged commits beneath origin/main — context the graph dims under
    the ship line. Reuses the unmerged commit shape; never mutates git."""
    if not _valid_base(base):
        return _bad_base()
    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    try:
        data = await runner.git_below_base(res.repo, base, limit)
    except GitTimeout:
        return _timeout_payload({"commits": [], "base": None})
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# GET /worktrees  (read-only porcelain parse)
# ---------------------------------------------------------------------------


@router.get("/git/{project_id}/worktrees")
async def git_worktrees_endpoint(
    request: Request,
    project_id: str = PathParam(...),
):
    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    try:
        data = await runner.git_worktrees(res.repo)
    except GitTimeout:
        return _timeout_payload({"worktrees": [], "count": 0})
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# GET /batches  (manifest → spec → auto-group)
# ---------------------------------------------------------------------------


@router.get("/git/{project_id}/batches")
async def git_batches_endpoint(
    request: Request,
    project_id: str = PathParam(...),
    base: str = Query("origin/main"),
):
    if not _valid_base(base):
        return _bad_base()
    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    repo = res.repo

    try:
        status = await runner.git_status(repo, base)
        branch = status.get("branch", "HEAD")
        _BRANCH_CACHE[str(repo)] = branch
        resolved_base, _ = await runner.resolve_base(repo, base)
        live = await runner.ordered_unmerged_shas(repo, resolved_base) if resolved_base else []
        total_unmerged = status.get("ahead", len(live))

        # Full sha → subject map so the ledger shows every batch commit's message,
        # independent of how much of the graph the client has loaded.
        subjects = await runner.commit_subjects(repo, base)

        # 1. Manifest — an existing plan is authoritative; reconcile to live SHAs.
        plan = store.load(project_id, branch)
        if plan is not None:
            store.reconcile_against_live(plan, live)
            # Gates are derived state — refresh any that never genuinely ran so a
            # stale persisted verdict can't outlive the condition it described.
            plan["gates"] = seed.refresh_gates(plan.get("gates"), branch)
            return JSONResponse(_public_plan(plan, total_unmerged, subjects))

        # 2/3. No plan — build in-memory from spec (or auto-group). Not persisted
        # until the operator makes a decision (lazy first write).
        plan = await _seed_fresh_plan(project_id, repo, base)
        return JSONResponse(_public_plan(plan, total_unmerged, subjects))
    except GitTimeout:
        return _timeout_payload({"batches": [], "total_unmerged": 0, "source": "timeout"})


# ---------------------------------------------------------------------------
# POST /batches/{batch_id}/decision  (durable — no git write)
# ---------------------------------------------------------------------------


@router.post("/git/{project_id}/batches/{batch_id}/decision")
async def git_batch_decision_endpoint(
    request: Request,
    body: DecisionRequest,
    project_id: str = PathParam(...),
    batch_id: str = PathParam(...),
    base: str = Query("origin/main"),
):
    if not _valid_base(base):
        return _bad_base()
    decision = (body.decision or "").strip().lower()
    if decision not in _VALID_DECISIONS:
        return JSONResponse(
            {"error": "invalid_decision", "allowed": sorted(_VALID_DECISIONS)},
            status_code=422,
        )
    new_status = (body.status or "").strip().lower() or None
    if new_status is not None and new_status not in _VALID_STATUSES:
        return JSONResponse(
            {"error": "invalid_status", "allowed": sorted(_VALID_STATUSES)},
            status_code=422,
        )

    res = await resolve_repo(request, project_id)
    if res.repo is None:
        return JSONResponse(res.payload, status_code=res.status_code)
    repo = res.repo

    try:
        status = await runner.git_status(repo, base)
        branch = status.get("branch", "HEAD")
        _BRANCH_CACHE[str(repo)] = branch
        resolved_base, _ = await runner.resolve_base(repo, base)
        live = await runner.ordered_unmerged_shas(repo, resolved_base) if resolved_base else []

        # Lazy first write: seed the full plan on the first decision, then apply.
        plan = store.load(project_id, branch)
        if plan is None:
            plan = await _seed_fresh_plan(project_id, repo, base)
        else:
            store.reconcile_against_live(plan, live)
            # Migrate stale derived gates on write, same as the read path.
            plan["gates"] = seed.refresh_gates(plan.get("gates"), branch)

        batch = store.find_batch(plan, batch_id)
        if batch is None:
            return JSONResponse(
                {"error": "batch_not_found", "batch_id": batch_id}, status_code=404
            )

        prev = batch.get("decision")
        batch["decision"] = decision
        batch["decided_by"] = "operator"
        batch["decided_at"] = int(time.time())
        if new_status is not None:
            batch["status"] = new_status
        if body.note:
            batch["note"] = body.note[:_NOTE_MAX]

        # Plan status advances out of "seeded" once the operator starts deciding.
        if plan.get("status") in (None, "draft", "seeded"):
            plan["status"] = "reviewing"

        store.append_history(
            plan,
            {
                "event": "decide",
                "batch": batch_id,
                "from": prev,
                "to": decision,
                "status": new_status,
            },
        )
        path = store.save(project_id, branch, plan)
        logger.info("ship plan updated: %s (%s -> %s)", path, prev, decision)

        return JSONResponse({"ok": True, "batch": batch, "plan_status": plan.get("status")})
    except GitTimeout:
        return _timeout_payload({"ok": False, "error": "git_timeout"})
