"""Classification taxonomy — closed vocabulary of context tags + Tier enum.

Design note: the original people-ontology-architecture spec in the vault
says "overlapping contexts allowed" — a person can carry multiple tags
simultaneously (family + close_friend + childhood). This module enforces
that via a closed vocabulary:

* CONTEXT_TAGS is a curated frozenset of allowed tag strings
* Tier is exclusive (one per person) — CORE, ACTIVE, etc.
* ClassificationResult holds both: one tier + a list of tags with
  per-tag confidence

The LLM classifier is constrained to produce tags from this vocabulary.
Any tag outside it is dropped at validation time. Operator corrections
are free-form but are also validated against the vocabulary.

Adding a new tag: edit CONTEXT_TAGS, update the docstring category, add
a test in test_taxonomy.py. That's it — no migration, no downstream
changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


# ── The closed vocabulary ────────────────────────────────────────────
#
# Tags are organized by conceptual category in the comments below but
# the runtime representation is a flat frozenset — categories are
# documentation only. A person can carry tags from multiple categories.

CONTEXT_TAGS: frozenset[str] = frozenset({
    # Kinship — family relationships
    "family_nuclear",          # parents, siblings, spouse, children
    "family_extended",         # cousins, uncles, aunts, grandparents
    "family_inlaw",            # spouse's family
    "family_chosen",           # not blood, functions as family

    # Friendship — personal warmth
    "close_friend",
    "friend",
    "childhood",               # known since childhood, regardless of current closeness

    # Work — professional context
    "colleague",
    "ex_colleague",
    "direct_report",
    "manager",
    "client",
    "vendor",
    "service_provider",
    "business_contact",
    "investor",
    "cofounder",

    # Community — groups the operator participates in
    "neighbor",
    "community_religious",
    "community_professional",  # alumni, industry groups
    "community_hobby",         # clubs, sports, interest groups
    "community_civic",         # volunteer, local govt

    # Mentorship — asymmetric knowledge/learning relationships
    "mentor",                  # person is a mentor to the operator
    "mentee",                  # operator mentors this person
    "peer_mentor",             # mutual, informal

    # State — where the relationship sits in time
    "emerging",                # new, recent, building
    "active",                  # current, regular contact
    "faded",                   # was close, not currently
    "dormant",                 # no recent activity at all

    # Specific kinds
    "acquaintance",            # known but not close
    "transactional",           # purely functional interaction
    "passing",                 # met briefly, minimal ongoing contact
})


# ── Tier — exclusive, deterministic ──────────────────────────────────

class Tier(Enum):
    """Relationship intensity tier. Exclusive — one per person."""

    CORE = "core"
    ACTIVE = "active"
    CHANNEL_SPECIFIC = "channel_specific"
    EMERGING = "emerging"
    FADING = "fading"
    DORMANT = "dormant"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, value: str | None) -> "Tier":
        """Parse a string into a Tier, falling back to UNKNOWN."""
        if not value:
            return cls.UNKNOWN
        normalized = value.strip().lower()
        for t in cls:
            if t.value == normalized:
                return t
        return cls.UNKNOWN


# Tier descriptions — for CLI display and UI tooltips.
TIER_DESCRIPTIONS: dict[Tier, str] = {
    Tier.CORE: "Core — high contact across multiple channels, recent",
    Tier.ACTIVE: "Active — regular contact, moderate intensity",
    Tier.CHANNEL_SPECIFIC: "Channel-specific — high density in one channel only",
    Tier.EMERGING: "Emerging — new, growing contact pattern",
    Tier.FADING: "Fading — was more active, trending down",
    Tier.DORMANT: "Dormant — no recent contact",
    Tier.UNKNOWN: "Unknown — insufficient data",
}


def pretty_tier(tier: Tier) -> str:
    """Human-friendly description for a tier."""
    return TIER_DESCRIPTIONS.get(tier, tier.value)


# ── Classification result ────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """A single classification for a person.

    Combines the deterministic tier (always from the rule classifier)
    with optional LLM-derived context tags. Rule-only results have
    empty tags and None model.
    """

    person_id: str
    tier: Tier
    context_tags: list[dict] = field(default_factory=list)
    # Each tag is {"tag": str, "confidence": float in [0, 1]}
    reasoning: str = ""
    model: str | None = None
    run_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = new_run_id()
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "tier": self.tier.value,
            "context_tags": list(self.context_tags),
            "reasoning": self.reasoning,
            "model": self.model,
            "run_id": self.run_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClassificationResult":
        return cls(
            person_id=data.get("person_id", ""),
            tier=Tier.from_str(data.get("tier")),
            context_tags=list(data.get("context_tags") or []),
            reasoning=data.get("reasoning") or "",
            model=data.get("model"),
            run_id=data.get("run_id") or "",
            created_at=data.get("created_at") or "",
        )


# ── Validation helpers ───────────────────────────────────────────────

def validate_tags(raw_tags: list[dict] | None) -> list[dict]:
    """Drop unknown tags, clamp confidences, dedupe by tag name.

    Accepts best-effort shapes:
        [{"tag": "family_nuclear", "confidence": 0.9}, ...]
        [{"name": "family_nuclear", "score": 0.9}, ...]  # tolerated
        ["family_nuclear", "close_friend"]               # shorthand
    """
    if not raw_tags:
        return []

    seen: set[str] = set()
    out: list[dict] = []

    for item in raw_tags:
        if isinstance(item, str):
            tag = item.strip().lower()
            conf = 1.0
        elif isinstance(item, dict):
            tag = (item.get("tag") or item.get("name") or "").strip().lower()
            raw_conf = item.get("confidence", item.get("score", 1.0))
            try:
                conf = float(raw_conf)
            except (TypeError, ValueError):
                conf = 1.0
        else:
            continue

        if not tag or tag not in CONTEXT_TAGS:
            continue
        if tag in seen:
            continue

        seen.add(tag)
        out.append({"tag": tag, "confidence": _clamp_confidence(conf)})

    return out


def _clamp_confidence(v: float) -> float:
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, round(float(v), 3)))


def new_run_id() -> str:
    """Generate a short, unique run identifier."""
    return f"run_{uuid.uuid4().hex[:10]}"
