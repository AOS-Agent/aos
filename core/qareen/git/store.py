"""Durable ship-plan store — the operator-owned half of the cockpit.

ONE plan file per branch at ``~/.aos/ship/<project>--<branch>.yaml``. Pure NEW
instance data under ~/.aos/, written on first seed/decision — so there is NO
instance-impacting migration for v1 (component-lifecycle clean: framework ships
the code, instance writes the plan lazily, runtime degrades gracefully when the
plan is absent).

Sentinel pattern: read returns None when absent (never raises); the first write
mkdirs the directory.

Data model (schema: 1):
  ShipPlan { schema, project, repo, branch, base, status, seed{}, batches[], gates{}, history[] }
  Batch    { id, ordinal, title, commit_count, commits[], status, decision,
             suggested_decision, suggested, rationale, watch_items[], assignment,
             decided_by, decided_at }
  Gate     { id, scope, status, summary, exit_code, ran_at, ran_against }

Two axes per batch are deliberate and never collapsed:
  status   = "is it built?"   (built | half-baked | broken | unknown) — from audit
  decision = "are we shipping?"(undecided | ship | defer | hold)      — operator
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml

SCHEMA_VERSION = 1

SHIP_DIR = Path.home() / ".aos" / "ship"

# Append-only history cap so the file can't grow unbounded.
_HISTORY_CAP = 200


def _slug(value: str) -> str:
    """Filesystem-safe slug for a project id or branch name."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in (value or "").strip())
    return safe.strip("-") or "unknown"


def plan_path(project: str, branch: str) -> Path:
    return SHIP_DIR / f"{_slug(project)}--{_slug(branch)}.yaml"


def load(project: str, branch: str) -> dict | None:
    """Load the plan for (project, branch), or None when absent/unreadable."""
    path = plan_path(project, branch)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save(project: str, branch: str, plan: dict) -> Path:
    """Persist the plan, mkdir'ing ~/.aos/ship on first write."""
    SHIP_DIR.mkdir(parents=True, exist_ok=True)
    plan.setdefault("schema", SCHEMA_VERSION)
    path = plan_path(project, branch)
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(plan, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)  # atomic-ish swap so a crash can't leave a half-written plan
    return path


def append_history(plan: dict, event: dict) -> None:
    """Append an event to the capped, append-only history list (in place)."""
    hist = plan.setdefault("history", [])
    event.setdefault("at", int(time.time()))
    hist.append(event)
    if len(hist) > _HISTORY_CAP:
        del hist[: len(hist) - _HISTORY_CAP]


def find_batch(plan: dict, batch_id: str) -> dict | None:
    for b in plan.get("batches", []):
        if b.get("id") == batch_id:
            return b
    return None


def reconcile_against_live(plan: dict, live_shas: list[str]) -> bool:
    """Reconcile pinned batch SHAs against the live unmerged set.

    Drops already-merged SHAs from each batch and flags drift on the plan. The
    join between derived git state and durable decisions is the stable SHA, so a
    rebase/merge that removes commits is detected here, not silently mis-rendered.

    Returns True if any drift was detected.
    """
    live = set(live_shas)
    drift = False
    for b in plan.get("batches", []):
        pinned = b.get("commits", []) or []
        kept = [s for s in pinned if s in live]
        if len(kept) != len(pinned):
            drift = True
            b["commits"] = kept
            b["commit_count_live"] = len(kept)
        else:
            b["commit_count_live"] = len(pinned)
    plan["drift"] = drift
    return drift
