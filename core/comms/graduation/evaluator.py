"""Graduation evaluator.

Evaluates whether a person's comms trust level should change based on
interaction history and operator feedback. Per-person, per-capability.

Levels:
  0 — OBSERVE: System watches, logs, learns. Operator sees nothing.
  1 — SURFACE: System alerts on missed/urgent messages. Operator sees.
  2 — DRAFT:   System drafts replies. Operator approves/edits before send.
  3 — ACT:     System handles routine messages. Operator gets daily digest.

Promotion requires sustained accuracy. Demotion is instant.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class GraduationResult:
    """Result of evaluating one person's trust graduation."""
    person_id: str
    person_name: str
    current_level: int
    recommended_level: int
    can_promote: bool = False
    can_demote: bool = False
    evidence: dict = field(default_factory=dict)
    reason: str = ""


def _get_current_level(person_id: str, trust_config: dict) -> int:
    """Get current comms trust level for a person from config."""
    per_person = trust_config.get("comms", {}).get("per_person", {})
    entry = per_person.get(person_id, {})
    if isinstance(entry, dict):
        return entry.get("level", 0)
    elif isinstance(entry, int):
        return entry
    return 0


def _get_thresholds(trust_config: dict) -> dict:
    """Get graduation thresholds from config."""
    return trust_config.get("comms", {}).get("thresholds", {
        "surface": {"min_interactions": 10, "acceptance_rate": 0.8},
        "draft": {"min_surfaces": 10, "acceptance_rate": 0.8},
        "act": {"min_drafts": 20, "acceptance_rate": 0.9},
    })


def _feedback_stats(conn: sqlite3.Connection, person_id: str, surface_type: str) -> dict:
    """Get feedback statistics for a person and surface type."""
    rows = conn.execute(
        "SELECT operator_action, COUNT(*) as cnt "
        "FROM surface_feedback "
        "WHERE person_id = ? AND surface_type = ? AND operator_action IS NOT NULL "
        "GROUP BY operator_action",
        (person_id, surface_type),
    ).fetchall()

    stats = {"total": 0, "accepted": 0, "edited": 0, "dismissed": 0, "ignored": 0}
    for r in rows:
        action = r["operator_action"]
        count = r["cnt"]
        stats["total"] += count
        if action in stats:
            stats[action] = count

    if stats["total"] > 0:
        # Accepted + edited count as positive (operator engaged)
        positive = stats["accepted"] + stats["edited"]
        stats["acceptance_rate"] = positive / stats["total"]
    else:
        stats["acceptance_rate"] = 0.0

    return stats


def evaluate_person(
    person_id: str,
    conn: sqlite3.Connection,
    trust_config: dict,
) -> GraduationResult:
    """Evaluate graduation status for a single person.

    Returns a GraduationResult with recommendation.
    """
    # Get person name
    person = conn.execute(
        "SELECT canonical_name FROM people WHERE id = ?", (person_id,)
    ).fetchone()
    name = person["canonical_name"] if person else person_id

    current = _get_current_level(person_id, trust_config)
    thresholds = _get_thresholds(trust_config)

    result = GraduationResult(
        person_id=person_id,
        person_name=name,
        current_level=current,
        recommended_level=current,
    )

    # Get interaction count
    ix_count = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE person_id = ?",
        (person_id,),
    ).fetchone()[0]

    result.evidence["interaction_count"] = ix_count

    # ── Check for demotion (always checked first) ────────

    if current >= 3:
        # Level 3: check for bad autonomous actions
        auto_stats = _feedback_stats(conn, person_id, "autonomous")
        result.evidence["autonomous_stats"] = auto_stats

        # Last 5 autonomous actions — if 2+ are dismissed/edited, demote
        recent = conn.execute(
            "SELECT operator_action FROM surface_feedback "
            "WHERE person_id = ? AND surface_type = 'autonomous' "
            "ORDER BY surface_at DESC LIMIT 5",
            (person_id,),
        ).fetchall()

        bad_count = sum(1 for r in recent if r["operator_action"] in ("dismissed", "edited"))
        if bad_count >= 2:
            result.recommended_level = 2
            result.can_demote = True
            result.reason = f"Demote: {bad_count}/5 recent autonomous actions corrected"
            return result

    if current >= 2:
        # Level 2: check for bad drafts
        draft_stats = _feedback_stats(conn, person_id, "draft")
        result.evidence["draft_stats"] = draft_stats

        if draft_stats["total"] >= 5 and draft_stats["acceptance_rate"] < 0.5:
            result.recommended_level = 1
            result.can_demote = True
            result.reason = f"Demote: draft acceptance rate {draft_stats['acceptance_rate']:.0%} < 50%"
            return result

    # ── Check for promotion ──────────────────────────────

    if current == 0:
        # Level 0 → 1: enough interactions?
        threshold = thresholds.get("surface", {})
        min_ix = threshold.get("min_interactions", 10)

        if ix_count >= min_ix:
            result.recommended_level = 1
            result.can_promote = True
            result.reason = f"Promote: {ix_count} interactions >= {min_ix} threshold"
            result.evidence["threshold"] = min_ix
        else:
            result.reason = f"Hold: {ix_count}/{min_ix} interactions needed for Level 1"

    elif current == 1:
        # Level 1 → 2: enough accepted surfaces?
        threshold = thresholds.get("draft", {})
        min_surfaces = threshold.get("min_surfaces", 10)
        min_rate = threshold.get("acceptance_rate", 0.8)

        surface_stats = _feedback_stats(conn, person_id, "surface")
        result.evidence["surface_stats"] = surface_stats

        if (surface_stats["total"] >= min_surfaces
                and surface_stats["acceptance_rate"] >= min_rate):
            result.recommended_level = 2
            result.can_promote = True
            result.reason = (
                f"Promote: {surface_stats['total']} surfaces, "
                f"{surface_stats['acceptance_rate']:.0%} acceptance >= {min_rate:.0%}"
            )
        else:
            result.reason = (
                f"Hold: {surface_stats['total']}/{min_surfaces} surfaces, "
                f"{surface_stats['acceptance_rate']:.0%}/{min_rate:.0%} rate"
            )

    elif current == 2:
        # Level 2 → 3: enough approved drafts?
        threshold = thresholds.get("act", {})
        min_drafts = threshold.get("min_drafts", 20)
        min_rate = threshold.get("acceptance_rate", 0.9)

        draft_stats = _feedback_stats(conn, person_id, "draft")
        result.evidence["draft_stats"] = draft_stats

        if (draft_stats["total"] >= min_drafts
                and draft_stats["acceptance_rate"] >= min_rate):
            result.recommended_level = 3
            result.can_promote = True
            result.reason = (
                f"Promote: {draft_stats['total']} drafts, "
                f"{draft_stats['acceptance_rate']:.0%} acceptance >= {min_rate:.0%}"
            )
        else:
            result.reason = (
                f"Hold: {draft_stats['total']}/{min_drafts} drafts, "
                f"{draft_stats['acceptance_rate']:.0%}/{min_rate:.0%} rate"
            )

    return result


def evaluate_all(
    conn: sqlite3.Connection,
    trust_config: dict,
) -> list[GraduationResult]:
    """Evaluate graduation for all people with interactions.

    Returns list of GraduationResults, sorted by actionable items first.
    """
    rows = conn.execute(
        "SELECT DISTINCT person_id FROM interactions"
    ).fetchall()

    results = []
    for row in rows:
        result = evaluate_person(row[0], conn, trust_config)
        results.append(result)

    # Sort: demotions first, then promotions, then holds
    results.sort(key=lambda r: (
        0 if r.can_demote else (1 if r.can_promote else 2),
        -r.current_level,
    ))

    return results
