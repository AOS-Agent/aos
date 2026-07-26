"""
Project Brief — shared dataclasses.

Dependency-free by design: this module imports nothing from AOS. Every other
brief-related module (brief.py, enrich.py, actor.py, the API layer) imports its
types from here, so the signatures below are a contract. See BRIEF-CONTRACT.md.

Nothing in here computes anything. It is a schema, not a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Actor",
    "Artifact",
    "Blocker",
    "Conflict",
    "Event",
    "NextItem",
    "Phase",
    "ProjectBrief",
    "STATES",
    "CONFLICT_KINDS",
    "ARTIFACT_KINDS",
    "EVENT_KINDS",
    "ACTOR_KINDS",
]


# ── Vocabularies (documentation, not enforcement) ───────────────────────

STATES = ("moving", "warm", "cold", "not_started", "blocked", "done")

CONFLICT_KINDS = (
    "duplicate_spine",
    "orphan_task",
    "stale_doc",
    "untracked_repo",
    "status_disagreement",   # emitted by the enricher
    "no_body",               # emitted by the enricher
)

ARTIFACT_KINDS = (
    "initiative", "spec", "decision", "council",
    "session", "commit", "file", "deck",
    # A git repository nested inside the project's linked path — often where
    # the work actually happens. Carries its remote URL as the excerpt.
    "repo",
)

EVENT_KINDS = (
    "task_done", "task_started", "task_created",
    "commit", "session", "handoff", "decision",
)

ACTOR_KINDS = ("operator", "agent", "cron", "import", "unknown")


# ── Attribution ─────────────────────────────────────────────────────────

@dataclass
class Actor:
    """Who did a thing. Never invent one — ``unknown`` is the honest answer."""

    kind: str = "unknown"          # operator | agent | cron | import | unknown
    name: str = "unknown"          # "operator" | "chief" | "advisor" | ...
    session_id: str | None = None
    at: str = ""                   # ISO8601


# ── Brief sub-types ─────────────────────────────────────────────────────

@dataclass
class Phase:
    key: str                       # slug
    label: str                     # "Phase 2 — The claim spine"
    task_ids: list[str] = field(default_factory=list)
    done: int = 0
    total: int = 0
    state: str = "not_started"     # not_started | in_progress | done | blocked


@dataclass
class NextItem:
    task_id: str
    title: str
    why: str                       # "nothing blocks it; 3 tasks cite it"
    priority: int = 3


@dataclass
class Blocker:
    task_id: str
    title: str
    blocked_on: str                # plain English
    since: str | None = None


@dataclass
class Conflict:
    kind: str                      # see CONFLICT_KINDS
    severity: str = "warn"         # warn | error
    message: str = ""              # plain English, actionable
    refs: list[str] = field(default_factory=list)   # task ids / file paths


@dataclass
class Artifact:
    kind: str                      # see ARTIFACT_KINDS
    title: str
    path: str                      # vault path, repo path, or session id
    date: str | None = None
    excerpt: str | None = None     # first meaningful line


@dataclass
class Event:
    at: str                        # ISO8601
    kind: str                      # see EVENT_KINDS
    text: str                      # plain English one-liner
    ref: str | None = None
    actor: Actor = field(default_factory=Actor)


# ── The brief ───────────────────────────────────────────────────────────

@dataclass
class ProjectBrief:
    # identity
    id: str
    title: str
    goal: str | None = None
    goal_title: str | None = None
    done_when: str | None = None
    appetite: str | None = None
    repo_path: str | None = None

    # derived state
    state: str = "cold"
    state_reason: str = ""
    last_activity: str | None = None
    last_activity_source: str = "unknown"   # task | git | session | handoff

    # counts (authoritative — from the project record, never from capped rows)
    task_count: int = 0
    done_count: int = 0
    active_count: int = 0
    todo_count: int = 0
    waiting_count: int = 0
    pct: int = 0

    # compiled content
    summary: str = ""
    narrative: str | None = None
    narrative_written_at: str | None = None
    narrative_aged: bool = False

    tags: list[str] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    next_up: list[NextItem] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    recent_activity: list[Event] = field(default_factory=list)

    # provenance
    sources: list[str] = field(default_factory=list)
    compiled_at: str = ""
    compile_ms: int = 0
