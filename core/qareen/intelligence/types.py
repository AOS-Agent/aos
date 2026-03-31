"""Qareen Intelligence — Type Definitions.

Types for the intelligence engine: intent classification, entity extraction,
context assembly, and card generation.

The intelligence engine processes natural-language utterances through a
tiered system:
  - Tier 0: regex/keyword intent classification (~2ms)
  - Tier 1: local model intent + entity extraction (~50ms)
  - Tier 2: LLM card generation with full context (~500ms)

Cards are the output — structured proposals for the operator to approve,
dismiss, or let expire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class Intent(str, Enum):
    """Recognized intent categories for operator utterances.

    Classified by Tier 0 (regex) or Tier 1 (local model).
    AMBIENT and FILLER are low-signal intents that skip Tier 2.
    UNKNOWN triggers a clarification card.
    """

    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    DECISION = "decision"
    RECALL = "recall"
    MESSAGE_SEND = "message_send"
    MESSAGE_REPLY = "message_reply"
    VAULT_CAPTURE = "vault_capture"
    SEARCH = "search"
    COMMAND = "command"
    AMBIENT = "ambient"
    FILLER = "filler"
    UNKNOWN = "unknown"


class CardType(str, Enum):
    """Card subtypes matching the card dataclass hierarchy."""

    TASK = "task"
    DECISION = "decision"
    VAULT = "vault"
    REPLY = "reply"
    SYSTEM = "system"
    SUGGESTION = "suggestion"


# ---------------------------------------------------------------------------
# Extracted entities
# ---------------------------------------------------------------------------

@dataclass
class ExtractedEntity:
    """A named entity extracted from an utterance.

    Entity types follow a fixed vocabulary: person, project, task,
    topic, number, date. The resolved_id is populated when the entity
    can be matched to an existing ontology object.

    Attributes:
        entity_type: One of person, project, task, topic, number, date.
        value: The raw text span that was extracted.
        resolved_id: The ontology object id if the entity was resolved
            to an existing record, or None if unresolved.
        confidence: Extraction confidence score, 0.0 to 1.0.
    """

    entity_type: str  # person | project | task | topic | number | date
    value: str
    resolved_id: str | None = None
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Intent result
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    """Result of intent classification on an utterance.

    Produced by classify_intent(). Contains the recognized intent,
    extracted entities, and which classification tier was used.

    Attributes:
        intent: The classified intent category.
        confidence: Classification confidence, 0.0 to 1.0.
        entities: Entities extracted from the utterance text.
        raw_text: The original utterance text that was classified.
        tier_used: Which classification tier produced this result.
            0 = regex/keyword, 1 = local model, 2 = LLM fallback.
    """

    intent: Intent
    confidence: float
    entities: list[ExtractedEntity] = field(default_factory=list)
    raw_text: str = ""
    tier_used: int = 0  # 0 = regex, 1 = local model, 2 = LLM


# ---------------------------------------------------------------------------
# Context packet — assembled context for LLM calls
# ---------------------------------------------------------------------------

@dataclass
class ContextPacket:
    """Assembled context for a Tier 2 LLM call.

    The intelligence engine builds this by querying the ontology,
    QMD, and work state. Each field has a target token budget to
    keep total context within the LLM's effective window.

    Attributes:
        system_context: System prompt and operator profile (~300 tokens).
        active_context: Currently active people, projects, initiatives
            (~500 tokens).
        rolling_transcript: Recent conversation transcript for continuity
            (~800 tokens).
        vault_context: Relevant QMD search results surfaced for this
            utterance (~800 tokens).
        work_state: Active tasks, blockers, recent completions
            (~400 tokens).
        current_utterance: The utterance being processed (~200 tokens).
        total_tokens: Estimated total token count across all fields.
    """

    system_context: str = ""       # ~300 tokens
    active_context: str = ""       # ~500 tokens
    rolling_transcript: str = ""   # ~800 tokens
    vault_context: str = ""        # ~800 tokens
    work_state: str = ""           # ~400 tokens
    current_utterance: str = ""    # ~200 tokens
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Cards — the intelligence engine's output
# ---------------------------------------------------------------------------

@dataclass
class Card:
    """Base card — a structured proposal surfaced to the operator.

    Cards are the primary output of the intelligence engine. Each card
    represents a proposed action, captured insight, or system notification.
    The operator can approve, dismiss, or let cards expire.

    Attributes:
        id: Unique card identifier (UUID).
        card_type: The card subtype discriminator (see CardType enum).
        title: Short human-readable title for the card.
        body: Longer description or explanation.
        status: Lifecycle state — pending, approved, dismissed, or expired.
        created_at: When the card was generated.
        expires_at: When the card auto-expires, or None for no expiry.
        source_utterance: The utterance that triggered this card, if any.
        confidence: How confident the engine is in this card (0.0 to 1.0).
    """

    id: str
    card_type: str
    title: str
    body: str = ""
    status: str = "pending"  # pending | approved | dismissed | expired
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    source_utterance: str | None = None
    confidence: float = 0.0


@dataclass
class TaskCard(Card):
    """Card proposing task creation or update.

    Attributes:
        task_title: Proposed task title.
        task_project: Target project for the task, if identified.
        task_priority: Proposed priority (1-5).
        task_assignee: Proposed assignee id, if identified.
        task_due: Proposed due date, if mentioned.
        task_parent_id: Parent task id for subtask proposals.
        is_update: True if updating an existing task, False if creating.
        existing_task_id: The id of the task being updated, if is_update.
    """

    task_title: str = ""
    task_project: str | None = None
    task_priority: int = 3
    task_assignee: str | None = None
    task_due: datetime | None = None
    task_parent_id: str | None = None
    is_update: bool = False
    existing_task_id: str | None = None


@dataclass
class DecisionCard(Card):
    """Card capturing a decision that should be recorded.

    Attributes:
        rationale: Why this decision was made or proposed.
        stakeholders: List of person ids affected by this decision.
        project: Project this decision is scoped to, if any.
        supersedes: Id of a previous decision this replaces, if any.
    """

    rationale: str = ""
    stakeholders: list[str] = field(default_factory=list)
    project: str | None = None
    supersedes: str | None = None


@dataclass
class VaultCard(Card):
    """Card proposing a vault note capture.

    Attributes:
        note_type: The vault note type — capture, research, reference,
            synthesis, decision, expertise.
        tags: Proposed tags for the vault note.
        project: Project scope for the note, if any.
        suggested_path: Proposed vault path for the note.
    """

    note_type: str = "capture"  # capture | research | reference | synthesis | decision | expertise
    tags: list[str] = field(default_factory=list)
    project: str | None = None
    suggested_path: str | None = None


@dataclass
class ReplyCard(Card):
    """Card proposing a message reply.

    Attributes:
        channel: The communication channel (telegram, whatsapp, email, etc.).
        recipient: Person id or name of the reply recipient.
        draft_text: The proposed reply text.
        thread_id: Message thread id for threaded replies.
        original_message_id: Id of the message being replied to.
    """

    channel: str = ""
    recipient: str = ""
    draft_text: str = ""
    thread_id: str | None = None
    original_message_id: str | None = None


@dataclass
class SystemCard(Card):
    """Card surfacing a system event or alert.

    Attributes:
        severity: Alert severity — info, warning, error, critical.
        service_name: The AOS service that triggered this card.
        suggested_action: What the operator should do, if anything.
        auto_resolve: Whether the system can resolve this automatically.
    """

    severity: str = "info"  # info | warning | error | critical
    service_name: str = ""
    suggested_action: str | None = None
    auto_resolve: bool = False


@dataclass
class SuggestionCard(Card):
    """Card surfacing a proactive suggestion based on patterns.

    Attributes:
        pattern: The behavioral or data pattern that triggered this.
        observation: What the engine observed that led to the suggestion.
        suggested_actions: List of concrete actions the operator could take.
        related_entities: Ontology object ids related to this suggestion.
    """

    pattern: str = ""
    observation: str = ""
    suggested_actions: list[str] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
