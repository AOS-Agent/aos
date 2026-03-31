"""Qareen Intelligence — Card Generator.

Takes an IntentResult from the classifier and generates the appropriate
Card instance. This is the Tier 2 output stage (though for Tier 0 regex
intents, it runs synchronously without an LLM call).

Each intent maps to a specific card type:
  TASK_CREATE  -> TaskCard
  TASK_UPDATE  -> TaskCard (is_update=True)
  DECISION     -> DecisionCard
  RECALL       -> SuggestionCard (pattern='recall')
  VAULT_CAPTURE -> VaultCard
  MESSAGE_SEND -> ReplyCard
  COMMAND      -> SystemCard
  FILLER/UNKNOWN -> None (no card generated)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from .types import (
    Card,
    DecisionCard,
    Intent,
    IntentResult,
    ReplyCard,
    SuggestionCard,
    SystemCard,
    TaskCard,
    VaultCard,
)


def generate_card(intent_result: IntentResult) -> Card | None:
    """Generate an approval card from a classified intent.

    Low-signal intents (FILLER, UNKNOWN, AMBIENT) return None.

    Args:
        intent_result: The classified intent with extracted entities.

    Returns:
        A Card subclass appropriate to the intent, or None if no
        card should be generated.
    """
    if intent_result.intent in (Intent.FILLER, Intent.UNKNOWN, Intent.AMBIENT):
        return None

    card_id = f"c-{uuid.uuid4().hex[:8]}"
    now = datetime.now()
    expires = now + timedelta(minutes=30)

    # Extract common fields from entities
    title_entity = next(
        (e for e in intent_result.entities if e.entity_type == "topic"), None
    )
    title = title_entity.value if title_entity else intent_result.raw_text

    project_entity = next(
        (e for e in intent_result.entities if e.entity_type == "project"), None
    )
    project = project_entity.resolved_id if project_entity else None

    priority_entity = next(
        (e for e in intent_result.entities if e.entity_type == "number"), None
    )
    priority = int(priority_entity.value) if priority_entity else 3

    # --- Dispatch by intent ---

    if intent_result.intent == Intent.TASK_CREATE:
        return TaskCard(
            id=card_id,
            card_type="task",
            title=f"Create task: {title}",
            body=f'Add "{title}" to {project or "inbox"}',
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            task_title=title,
            task_project=project,
            task_priority=priority,
        )

    if intent_result.intent == Intent.TASK_UPDATE:
        return TaskCard(
            id=card_id,
            card_type="task",
            title=f"Complete: {title}",
            body=f'Mark "{title}" as done',
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            task_title=title,
            task_project=project,
            task_priority=priority,
            is_update=True,
        )

    if intent_result.intent == Intent.DECISION:
        return DecisionCard(
            id=card_id,
            card_type="decision",
            title=f"Lock decision: {title}",
            body=title,
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            rationale=title,
            project=project,
        )

    if intent_result.intent == Intent.VAULT_CAPTURE:
        return VaultCard(
            id=card_id,
            card_type="vault",
            title=f"Capture: {title}",
            body=title,
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            note_type="capture",
            project=project,
        )

    if intent_result.intent == Intent.RECALL:
        return SuggestionCard(
            id=card_id,
            card_type="suggestion",
            title=f"Search: {title}",
            body=f'Looking for information about "{title}"',
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            pattern="recall",
            observation=f"Searching vault and ontology for: {title}",
        )

    if intent_result.intent == Intent.COMMAND:
        return SystemCard(
            id=card_id,
            card_type="system",
            title=f"Command: {title}",
            body=title,
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            severity="info",
        )

    if intent_result.intent in (Intent.MESSAGE_SEND, Intent.MESSAGE_REPLY):
        # Extract recipient from entities
        recipient_entity = next(
            (e for e in intent_result.entities if e.entity_type == "person"), None
        )
        recipient = recipient_entity.value if recipient_entity else ""
        return ReplyCard(
            id=card_id,
            card_type="reply",
            title=f"Send message to {recipient}" if recipient else "Send message",
            body=title,
            status="pending",
            created_at=now,
            expires_at=expires,
            source_utterance=intent_result.raw_text,
            confidence=intent_result.confidence,
            recipient=recipient,
            draft_text=title,
        )

    return None
