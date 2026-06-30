"""Seed a ship plan from the operator's commit-triage spec.

The join between derived git state and durable decisions. Batch SOURCE priority:

  1. Manifest  — an existing plan yaml is authoritative (handled in the API layer
                 via store.load + reconcile; this module is only the *builder*).
  2. Spec      — parse ~/vault/knowledge/specs/<branch>-commit-triage.md, the
                 operator's vault (NOT the repo). The markdown table
                 ``| # | Batch | Commits | Rec |`` gives ordinal/title/count + a
                 leading-bold Rec keyword (SHIP/DEFER/HOLD/DROP → suggested
                 decision) + prose rationale. Watch-items + Decisions-needed lines
                 attach to their batches. The spec carries NO SHAs, so SHAs are
                 assigned by consuming each batch's commit_count oldest→newest from
                 the live unmerged list (assignment: "inferred-by-count").
  3. Auto-group — no spec: bucket consecutive commits by conventional-commit scope;
                  status unknown, decision undecided.

ship-map.md's "2 real blockers" seed PLAN-LEVEL failing gates (tsc, migration-safety).
BROKEN clusters seed gate failures rather than guessing a batch↔cluster mapping
(clusters are thematic, batches sequential — don't force a false mapping).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

SPECS_DIR = Path.home() / "vault" / "knowledge" / "specs"

# Rec keyword (bold) → operator's *suggested* decision.
_KEYWORD_DECISION = {
    "SHIP": "ship",
    "DEFER": "defer",
    "HOLD": "hold",
    "DROP": "hold",
}

_BOLD_KEYWORD = re.compile(r"\*\*\s*([A-Z]+)\s*\*\*")
_BATCH_REF = re.compile(r"Batch\s+(\d+)")


def triage_path(branch: str) -> Path:
    return SPECS_DIR / f"{branch}-commit-triage.md"


def shipmap_path(branch: str) -> Path:
    return SPECS_DIR / f"{branch}-ship-map.md"


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def _parse_table_rows(text: str) -> list[dict]:
    """Parse the ``| # | Batch | Commits | Rec |`` rows."""
    rows: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.split("|")]
        # cells[0] and cells[-1] are empty bookends from the outer pipes.
        cells = [c for c in cells]
        if len(cells) < 6:
            continue
        ordinal_cell, title_cell, count_cell, rec_cell = cells[1], cells[2], cells[3], cells[4]
        if not ordinal_cell.isdigit():
            continue  # skips header + separator rows
        try:
            count = int(re.sub(r"[^0-9]", "", count_cell) or "0")
        except ValueError:
            count = 0

        keyword = "SHIP"
        m = _BOLD_KEYWORD.search(rec_cell)
        if m:
            keyword = m.group(1).upper()
        # Rationale = the Rec prose minus the leading bold keyword token.
        rationale = _BOLD_KEYWORD.sub("", rec_cell, count=1).strip()
        rationale = rationale.lstrip("—-– ").strip()

        rows.append(
            {
                "ordinal": int(ordinal_cell),
                "title": title_cell,
                "count": count,
                "keyword": keyword,
                "suggested_decision": _KEYWORD_DECISION.get(keyword, "ship"),
                "rationale": rationale,
            }
        )
    return rows


def _parse_attention_lines(text: str) -> dict[int, list[str]]:
    """Collect Watch-items + Decisions-needed bullets keyed by batch ordinal."""
    out: dict[int, list[str]] = {}
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("-") or s.startswith("*")):
            continue
        refs = _BATCH_REF.findall(s)
        if not refs:
            continue
        bullet = s.lstrip("-*").strip()
        bullet = re.sub(r"\*\*", "", bullet)  # strip md bold for chip display
        for r in refs:
            out.setdefault(int(r), []).append(bullet)
    return out


def _audit_status(keyword: str) -> str:
    """Seed the AUDIT axis (built/unknown) — honest, no false batch↔cluster map.

    In-branch SHIP work is BUILT per the triage audit cross-ref; DEFER/HOLD work
    (n8n, council substrate) is not yet validated, so it stays unknown.
    """
    return "built" if keyword == "SHIP" else "unknown"


# ---------------------------------------------------------------------------
# Gate seeding (ship-map blockers)
# ---------------------------------------------------------------------------


def _seed_gates(branch: str) -> dict:
    """Plan-level gates, seeded as NOT-YET-RUN.

    Gate EXECUTION is a deferred follow-on (the streaming ``POST /gates/run``).
    Until a gate actually runs we must NOT assert pass/fail: a hardcoded verdict —
    even one sourced from a ship-map's known blockers — goes stale the instant the
    blocker is fixed and then lies to the operator (e.g. showing "tsc: ~25 errors"
    long after tsc is green). Every gate therefore starts ``unknown`` / "not run";
    only a real run is allowed to move it. The ``branch`` arg is retained for the
    future per-branch gate config but no longer drives a hardcoded status.
    """

    def gate(gate_id: str) -> dict:
        return {
            "id": gate_id,
            "scope": "plan",
            "status": "unknown",
            "summary": "not run",
            "exit_code": None,
            "ran_at": None,
            "ran_against": None,
            "source": "unrun",
        }

    return {
        "tsc": gate("tsc"),
        "ship-check": gate("ship-check"),
        "migration-safety": gate("migration-safety"),
    }


def refresh_gates(existing: dict | None, branch: str) -> dict:
    """Re-seed gates that have NOT genuinely run, preserving real run results.

    Gates are derived system state, not operator-owned: a not-yet-run gate must
    never keep a verdict frozen in the plan yaml (that's how the old hardcoded
    "tsc: ~25 errors" survived long after tsc went green). A REAL run stamps
    ``ran_against`` with the HEAD sha it ran on; we treat that as the only mark of
    a genuine result. Anything without it reverts to the not-run seed. Once gate
    execution lands, its stamped results flow straight through here untouched.
    """
    fresh = _seed_gates(branch)
    if not existing:
        return fresh
    out: dict = {}
    for gid, seeded in fresh.items():
        prev = existing.get(gid)
        out[gid] = prev if (prev and prev.get("ran_against")) else seeded
    return out


# ---------------------------------------------------------------------------
# Auto-group fallback (no spec)
# ---------------------------------------------------------------------------


def _conventional_scope(subject: str) -> str:
    m = re.match(r"^([a-z]+)(?:\(([^)]+)\))?:", subject.strip(), re.IGNORECASE)
    if not m:
        return "misc"
    return (m.group(2) or m.group(1) or "misc").lower()


def _auto_group(ordered_shas: list[str], subjects: dict[str, str]) -> list[dict]:
    """Bucket consecutive commits by conventional-commit scope."""
    batches: list[dict] = []
    cur_scope: str | None = None
    cur: list[str] = []

    def flush() -> None:
        if cur:
            idx = len(batches) + 1
            batches.append(
                {
                    "id": f"batch-{idx:02d}",
                    "ordinal": idx,
                    "title": (cur_scope or "misc").title(),
                    "commit_count": len(cur),
                    "commits": list(cur),
                    "status": "unknown",
                    "decision": "undecided",
                    "suggested_decision": "undecided",
                    "suggested": False,
                    "rationale": "",
                    "watch_items": [],
                    "assignment": "auto-group",
                    "decided_by": None,
                    "decided_at": None,
                }
            )

    for sha in ordered_shas:
        scope = _conventional_scope(subjects.get(sha, ""))
        if cur_scope is None:
            cur_scope = scope
        if scope != cur_scope:
            flush()
            cur = []
            cur_scope = scope
        cur.append(sha)
    flush()
    return batches


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_plan(
    project: str,
    repo: str,
    branch: str,
    base: str,
    ordered_shas: list[str],
    ahead: int,
    behind: int,
    head: str,
    subjects: dict[str, str] | None = None,
) -> dict:
    """Build a fresh ShipPlan dict (NOT persisted — the API layer decides that).

    ``ordered_shas`` is the live unmerged set oldest→newest. ``subjects`` maps sha
    → subject, used only by the auto-group fallback.
    """
    now = int(time.time())
    subjects = subjects or {}
    tpath = triage_path(branch)
    drift = False
    overflow = False

    if tpath.exists():
        text = tpath.read_text(encoding="utf-8", errors="replace")
        rows = _parse_table_rows(text)
        attention = _parse_attention_lines(text)
        source = "spec"

        batches: list[dict] = []
        cursor = 0
        n = len(ordered_shas)
        for row in rows:
            count = row["count"]
            shas = ordered_shas[cursor : cursor + count]
            cursor += count
            batches.append(
                {
                    "id": f"batch-{row['ordinal']:02d}",
                    "ordinal": row["ordinal"],
                    "title": row["title"],
                    "commit_count": count,
                    "commits": shas,
                    "status": _audit_status(row["keyword"]),
                    "decision": "undecided",
                    "suggested_decision": row["suggested_decision"],
                    "suggested": True,
                    "rationale": row["rationale"],
                    "watch_items": attention.get(row["ordinal"], []),
                    "assignment": "inferred-by-count",
                    "decided_by": None,
                    "decided_at": None,
                }
            )

        # Counts don't sum to the real ahead → dump the remainder into a synthetic
        # overflow batch + flag drift (never silently lose commits).
        if cursor < n:
            overflow = True
            drift = True
            remainder = ordered_shas[cursor:]
            idx = len(batches) + 1
            batches.append(
                {
                    "id": f"batch-{idx:02d}",
                    "ordinal": idx,
                    "title": "Uncategorized (overflow)",
                    "commit_count": len(remainder),
                    "commits": remainder,
                    "status": "unknown",
                    "decision": "undecided",
                    "suggested_decision": "undecided",
                    "suggested": False,
                    "rationale": "Commits beyond the spec's batch counts — re-seed the triage to categorize.",
                    "watch_items": [],
                    "assignment": "overflow",
                    "decided_by": None,
                    "decided_at": None,
                }
            )
        elif cursor > n:
            # Spec claims more commits than exist (already-merged drift). Trim and flag.
            drift = True
            for b in batches:
                b["commits"] = [s for s in b["commits"] if s in set(ordered_shas)]
    else:
        source = "auto-group"
        batches = _auto_group(ordered_shas, subjects)

    plan = {
        "schema": 1,
        "project": project,
        "repo": repo,
        "branch": branch,
        "base": base,
        "status": "seeded",
        "source": source,
        "drift": drift,
        "overflow": overflow,
        "seed": {
            "source_refs": [
                f"{branch}-commit-triage.md",
                f"{branch}-ship-map.md",
            ],
            "seeded_at": now,
            "seeded_head": head,
            "ahead": ahead,
            "behind": behind,
        },
        "batches": batches,
        "gates": _seed_gates(branch),
        "history": [
            {"event": "seed", "source": source, "batches": len(batches), "at": now},
        ],
    }
    return plan
