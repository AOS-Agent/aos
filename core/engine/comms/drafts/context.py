"""Context assembler for reply drafting.

Gathers everything needed to draft an intelligent reply:
- Recent conversation messages (what's being discussed)
- Person profile (who they are, relationship, importance)
- Communication patterns (response time, preferred hours, style)
- Operator's recent outbound messages to this person (voice/style samples)
- Style edit history (how the operator corrected past drafts)

The assembled context is a dataclass that the drafter consumes.
Never crashes — missing data becomes None with a flag.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
STYLE_EDITS_DIR = Path.home() / ".aos" / "work" / "comms" / "style_edits"


@dataclass
class DraftContext:
    """Everything the drafter needs to compose a reply."""

    # Who is this person?
    person_id: str
    person_name: str
    relationship: str = ""          # "sister", "brother-in-law", "friend"
    importance: int = 3

    # What are they saying?
    recent_messages: list[dict] = field(default_factory=list)  # Last 10 messages in convo
    last_inbound: str = ""          # The specific message we're replying to

    # How do you usually talk to them?
    patterns: dict | None = None    # From communication_patterns table
    style_samples: list[str] = field(default_factory=list)  # Operator's recent outbound
    style_edits: list[dict] = field(default_factory=list)   # Past draft corrections

    # Channel context
    channel: str = ""
    conversation_id: str = ""

    # Completeness flags
    has_patterns: bool = False
    has_style_samples: bool = False
    has_edit_history: bool = False

    def to_prompt_context(self) -> str:
        """Format as a text block the drafter can include in its prompt."""
        lines = []

        lines.append(f"## Person: {self.person_name}")
        if self.relationship:
            lines.append(f"Relationship: {self.relationship}")
        lines.append(f"Importance: {self.importance} (1=inner circle, 4=peripheral)")
        lines.append(f"Channel: {self.channel}")
        lines.append("")

        if self.recent_messages:
            lines.append("## Recent conversation")
            for msg in self.recent_messages[-10:]:
                sender = "You" if msg.get("from_me") else self.person_name
                lines.append(f"  {sender}: {msg.get('text', '')}")
            lines.append("")

        if self.patterns:
            lines.append("## Communication patterns with this person")
            p = self.patterns
            if p.get("avg_response_time_mins"):
                lines.append(f"  Your avg response time: {p['avg_response_time_mins']} mins")
            if p.get("style_brief_ratio") is not None:
                pct = int(p["style_brief_ratio"] * 100)
                lines.append(f"  Brief message ratio: {pct}%")
            if p.get("preferred_hours"):
                hrs = p["preferred_hours"] if isinstance(p["preferred_hours"], list) else []
                if hrs:
                    lines.append(f"  Active hours: {hrs}")
            lines.append("")

        if self.style_samples:
            lines.append("## Your recent messages to this person (match this voice)")
            for sample in self.style_samples[:5]:
                lines.append(f"  You: {sample}")
            lines.append("")

        if self.style_edits:
            lines.append("## How you corrected past drafts (learn from these)")
            for edit in self.style_edits[:3]:
                lines.append(f"  Draft was: {edit.get('original', '')}")
                lines.append(f"  You changed to: {edit.get('final', '')}")
            lines.append("")

        return "\n".join(lines)


def assemble_context(
    person_id: str,
    conversation_id: str,
    channel: str,
    conn: sqlite3.Connection,
    last_inbound: str = "",
    recent_messages: list[dict] | None = None,
) -> DraftContext:
    """Assemble full drafting context for a person.

    Args:
        person_id: Who we're replying to
        conversation_id: Which conversation
        channel: Which channel (whatsapp, imessage, telegram)
        conn: People DB connection
        last_inbound: The message we're replying to
        recent_messages: Pre-fetched recent messages (optional)

    Returns:
        DraftContext with all available data. Never crashes.
    """
    ctx = DraftContext(
        person_id=person_id,
        person_name="Unknown",
        channel=channel,
        conversation_id=conversation_id,
        last_inbound=last_inbound,
    )

    # ── Person profile ───────────────────────────────────
    try:
        person = conn.execute(
            "SELECT canonical_name, importance, nickname FROM people WHERE id = ?",
            (person_id,),
        ).fetchone()
        if person:
            ctx.person_name = person["canonical_name"]
            ctx.importance = person["importance"] or 3
    except Exception as e:
        log.debug(f"Failed to load person: {e}")

    # ── Relationship ─────────────────────────────────────
    try:
        # Find the operator's person_id (importance = 5 = self)
        operator = conn.execute(
            "SELECT id FROM people WHERE importance = 5 LIMIT 1"
        ).fetchone()
        if operator:
            rel = conn.execute(
                "SELECT subtype, context FROM relationships "
                "WHERE person_a_id = ? AND person_b_id = ?",
                (operator["id"], person_id),
            ).fetchone()
            if rel:
                ctx.relationship = rel["subtype"] or rel["context"] or ""
    except Exception as e:
        log.debug(f"Failed to load relationship: {e}")

    # ── Recent messages ──────────────────────────────────
    if recent_messages:
        ctx.recent_messages = recent_messages[-10:]

    # ── Communication patterns ───────────────────────────
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from patterns.query import get_pattern
        ctx.patterns = get_pattern(person_id, conn)
        ctx.has_patterns = ctx.patterns is not None
    except Exception as e:
        log.debug(f"Failed to load patterns: {e}")

    # ── Style samples (operator's recent outbound) ───────
    try:
        conn.execute(
            "SELECT occurred_at FROM interactions "
            "WHERE person_id = ? AND direction IN ('outbound', 'both') "
            "ORDER BY occurred_at DESC LIMIT 5",
            (person_id,),
        ).fetchall()
        # We don't have message text in interactions (just metadata).
        # Style samples come from the adapter at call time, or from
        # the recent_messages list passed in.
        if recent_messages:
            outbound = [m["text"] for m in recent_messages if m.get("from_me") and m.get("text")]
            ctx.style_samples = outbound[:5]
            ctx.has_style_samples = len(ctx.style_samples) >= 3
    except Exception as e:
        log.debug(f"Failed to load style samples: {e}")

    # ── Style edit history ───────────────────────────────
    try:
        edit_file = STYLE_EDITS_DIR / f"{person_id}.jsonl"
        if edit_file.exists():
            edits = []
            for line in edit_file.read_text().strip().split("\n"):
                if line:
                    edits.append(json.loads(line))
            ctx.style_edits = edits[-5:]  # Last 5 edits
            ctx.has_edit_history = len(ctx.style_edits) > 0
    except Exception as e:
        log.debug(f"Failed to load style edits: {e}")

    return ctx
