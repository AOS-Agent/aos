"""Pipeline definitions — per-pipeline stage models layered on the typed-state spine.

The board's machine spine is the six-value ``statuses.category`` enum
(``triage | backlog | unstarted | started | completed | cancelled``, locked in
spec §3.2 and seeded by Kanban Phase 0). Automation keys off *category*, never
off a column name.

A *pipeline* is a named, ordered set of fine-grained stages that live on top of
that spine. The first one is ``bug`` — islah's proven 13-state fix loop
(``schema.yaml``: new → triaging → confirmed/needs-* → fixing → verifying →
awaiting-approval → approved → shipped, plus reopened/duplicate/wont-fix). Rather
than flatten islah's richness into the coarse enum (the impedance risk called out
in the islah-unification dossier §7 risk-1), a bug task carries BOTH:

  * ``pipeline='bug'`` + ``pipeline_stage='fixing'`` — the fine islah state, and
  * a coarse ``status`` (a generic board column id) synced from the stage here.

Storing the coarse status keeps every existing status-filtered query cheap and
unchanged (board_tasks, project counts, summary, the CLI list filters) while the
fine stage drives the card's sub-label and the future runner's queue. Full
per-bug richness (root_cause, code_refs, attempts, proof, gate results) lives in
the ``tasks.fields`` JSON column and, from Phase 2, the append-only activity log —
never crammed into fixed columns.

This module is the SINGLE source of truth for that mapping. The Phase 1 migration
seeds the ``statuses`` table from it, and the WorkAdapter reads it for stage
transitions, so the DB and the code cannot drift.

Bug-pipeline stages are FRAMEWORK-defined (islah's model is universal to the bug
domain). The per-app registry — which repo/scheme/bundle a bug maps to — is
INSTANCE data and lives in the apps registry (config/apps.yaml + the instance
override), not here.
"""

from __future__ import annotations

# ── The bug pipeline ────────────────────────────────────────────────────────
#
# Each stage: id, human label, the six-value category it sits in, the coarse
# generic board status it syncs to, a color, and its ordered position. The
# category is the machine spine; the coarse status is which board column the
# bug card renders in. Two stages can share a category but differ in coarse
# status (needs-info/needs-decision → waiting vs fixing/verifying → active),
# which is exactly why the coarse status is explicit rather than derived from
# the category alone.

BUG_PIPELINE_ID = "bug"

# (stage_id, label, category, coarse_status, color, position)
BUG_STAGES: list[tuple[str, str, str, str, str, int]] = [
    ("new",               "New",               "triage",    "triage",    "#BF5AF2", 0),
    ("triaging",          "Triaging",          "triage",    "triage",    "#BF5AF2", 1),
    ("needs-info",        "Needs Info",        "started",   "waiting",   "#FFD60A", 2),
    ("confirmed",         "Confirmed",         "unstarted", "todo",      "#6B6560", 3),
    ("needs-decision",    "Needs Decision",    "started",   "waiting",   "#FFD60A", 4),
    ("fixing",            "Fixing",            "started",   "active",    "#0A84FF", 5),
    ("verifying",         "Verifying",         "started",   "active",    "#5E5CE6", 6),
    ("awaiting-approval", "Awaiting Approval", "started",   "in_review", "#BF5AF2", 7),
    ("approved",          "Approved",          "completed", "done",      "#30D158", 8),
    ("shipped",           "Shipped",           "completed", "done",      "#30D158", 9),
    ("reopened",          "Reopened",          "unstarted", "todo",      "#FF9F0A", 10),
    ("duplicate",         "Duplicate",         "cancelled", "cancelled", "#6B6560", 11),
    ("wont-fix",          "Won't Fix",         "cancelled", "cancelled", "#6B6560", 12),
]

# The stage a bug lands in when work actually begins (delegation to a fix agent).
BUG_STAGE_ACTIVE = "fixing"
# The stage a freshly-filed bug enters the board at.
BUG_STAGE_INTAKE = "new"

_STAGE_INDEX = {s[0]: s for s in BUG_STAGES}


def bug_stages() -> list[tuple[str, str, str, str, str, int]]:
    """The ordered bug stages. Each: (id, label, category, coarse_status, color, position)."""
    return list(BUG_STAGES)


def is_bug_stage(stage: str | None) -> bool:
    return stage in _STAGE_INDEX


def bug_stage_to_status(stage: str | None) -> str | None:
    """The coarse generic board status a bug stage syncs to, or None if unknown."""
    row = _STAGE_INDEX.get(stage or "")
    return row[3] if row else None


def bug_stage_category(stage: str | None) -> str | None:
    """The six-value category a bug stage sits in, or None if unknown."""
    row = _STAGE_INDEX.get(stage or "")
    return row[2] if row else None


def bug_stage_label(stage: str | None) -> str | None:
    row = _STAGE_INDEX.get(stage or "")
    return row[1] if row else None


# ── Generic board statuses ──────────────────────────────────────────────────
#
# The coarse board columns every task (bug or not) can occupy. This is the
# Phase 0 seed plus ``in_review`` (started), which Phase 1 adds so a bug in
# ``awaiting-approval`` — and any task pending human review — has a real column.
# The migration and the adapter both seed from this so they cannot drift.

# (id, name, category, color, position, is_default)
GENERIC_STATUSES: list[tuple[str, str, str, str, int, int]] = [
    ("triage",    "Triage",      "triage",    "#BF5AF2", 0, 0),
    ("backlog",   "Backlog",     "backlog",   "#6B6560", 1, 0),
    ("todo",      "Todo",        "unstarted", "#6B6560", 2, 1),
    ("active",    "In Progress", "started",   "#0A84FF", 3, 0),
    ("waiting",   "Waiting",     "started",   "#FFD60A", 4, 0),
    ("in_review", "In Review",   "started",   "#BF5AF2", 5, 0),
    ("done",      "Done",        "completed", "#30D158", 6, 1),
    ("cancelled", "Cancelled",   "cancelled", "#6B6560", 7, 0),
]

# Categories whose tasks are "open" — shown in the board's working columns.
# triage is a separate plane (spec §3.6); backlog is cold storage; completed /
# cancelled are the closed tail. board_tasks keys off this, not a status name.
OPEN_CATEGORIES = ("unstarted", "started")
