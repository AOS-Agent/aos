"""Task activity — the NARRATIVE layer of the work system (Kanban Phase 2).

The work system keeps two audit layers, deliberately kept separate — do not
merge them (spec §3.3, islah-unification dossier §7 risk-1):

  ``entity_history`` — the **FORENSIC** layer. One row per changed *field*
      (who / when / field / old → new). Exhaustive and machine-precise; the
      answer to "exactly what value changed". Written by
      ``WorkAdapter._record_history``.

  ``task_activity`` — the **NARRATIVE** layer (this module). One row per
      *logical event*, carrying a human-readable one-liner (``body``) and a
      structured ``data`` payload. It is the card's story — "Delegated to
      advisor", "Attempt 1 — build passed, tests failed", "Proof: lastPage
      fail → pass". A single delegation writes ~4 ``entity_history`` rows but
      exactly ONE ``task_activity`` line.

Why both exist: a field-change log answers *what field changed*; a narrative
answers *what happened, and why*. Flattening islah's ``attempts[]`` / ``proof[]``
richness down into field diffs would destroy the story. The narrative is what a
human (or the Phase 8 review queue) reads; the forensic log is what an auditor
greps.

Invariants (enforced in code AND by SQL triggers on the table):
  * **Append-only, immutable.** The adapter exposes ``append_activity`` and
    ``list_activity`` — never an update or delete of a past entry. Rewriting
    history is impossible by construction (this is islah's ``attempts[]``
    discipline generalised).
  * **Status is derived, never written around the log.** Every mutation that
    moves a task narrates the move; the coarse ``status`` column is a
    *consequence* of that narration, so the transcript and the state machine
    cannot disagree.
"""

from __future__ import annotations

# The full activity vocabulary. Auto-narration (inside the adapter's mutation
# choke point) writes created/status_changed/delegated/held/edited/linked;
# agents and the operator append the rest by hand via the CLI/API.
ACTIVITY_KINDS: tuple[str, ...] = (
    "created",
    "status_changed",
    "delegated",
    "held",
    "comment",
    "attempt",
    "proof",
    "blocked",
    "unblocked",
    "edited",
    "linked",
)

# Kinds an agent/operator may append by hand. The auto-narration kinds are
# refused on the manual path so a caller can never forge a system narration
# (a hand-written "status_changed" that the state machine never made).
APPENDABLE_KINDS: tuple[str, ...] = (
    "comment",
    "attempt",
    "proof",
    "blocked",
    "unblocked",
    "linked",
)

# status id → the verb used when narrating a status_changed line. Keyed off the
# generic board status the task lands in (bug fine-stages sync to one of these).
_STATUS_VERB: dict[str, str] = {
    "triage": "Moved to triage",
    "backlog": "Moved to backlog",
    "todo": "Moved to todo",
    "active": "Started",
    "waiting": "Waiting on input",
    "in_review": "Sent for review",
    "done": "Completed",
    "cancelled": "Cancelled",
}


def status_body(new_status: str, *, stage: str | None = None) -> str:
    """Human one-liner for a status transition. Bug stage refines the verb."""
    if stage:
        label = stage.replace("-", " ")
        return f"Moved to {label}"
    return _STATUS_VERB.get(new_status, f"Moved to {new_status}")


def is_appendable(kind: str) -> bool:
    return kind in APPENDABLE_KINDS


def is_valid_kind(kind: str) -> bool:
    return kind in ACTIVITY_KINDS


def actor_type_of(actor: str) -> str:
    """Classify an actor string into operator | agent | system for the UI avatar."""
    if actor.startswith("agent:"):
        return "agent"
    if actor.startswith("system:"):
        return "system"
    if actor in ("operator", "cli", "user"):
        return "operator"
    # A bare agent name (e.g. "advisor") passed by the runner counts as an agent.
    return "operator" if actor == "operator" else "agent"
