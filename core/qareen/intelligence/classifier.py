"""Qareen Intelligence — Tier 0 Regex Classifier.

Classifies operator utterances using regex patterns. This is the fastest
tier (~2ms) and catches explicit commands like "add task:", "note:",
"decide:", etc.

If no regex matches, returns FILLER (for short inputs) or UNKNOWN
(for longer inputs that may need Tier 1/2 processing).
"""

from __future__ import annotations

import re

from .types import ExtractedEntity, Intent, IntentResult

# ---------------------------------------------------------------------------
# Intent patterns — order matters (first match wins)
# ---------------------------------------------------------------------------

PATTERNS: list[tuple[re.Pattern[str], Intent]] = [
    # Task creation — explicit prefix
    (re.compile(r"(?:add|create|new)\s+task[:\s]+(.+)", re.IGNORECASE), Intent.TASK_CREATE),
    (re.compile(r"(?:todo|to-do|to do)[:\s]+(.+)", re.IGNORECASE), Intent.TASK_CREATE),
    # Task creation — natural language
    (re.compile(r"(?:add|create)\s+(?:a\s+)?task\s+(?:to|for|about)\s+(.+)", re.IGNORECASE), Intent.TASK_CREATE),
    (re.compile(r"(?:i need to|i have to|i should|remind me to|don't forget to)\s+(.+)", re.IGNORECASE), Intent.TASK_CREATE),
    (re.compile(r"(?:can you|please)\s+(?:add|create)\s+(?:a\s+)?(?:task|todo)\s+(?:to|for|about)?\s*(.+)", re.IGNORECASE), Intent.TASK_CREATE),

    # Task completion / update
    (re.compile(r"(?:done|complete|finish|mark done)[:\s]+(.+)", re.IGNORECASE), Intent.TASK_UPDATE),
    (re.compile(r"(?:i (?:just )?(?:finished|completed|did)|that's done)[:\s]*(.+)", re.IGNORECASE), Intent.TASK_UPDATE),

    # Recall / search — must come before decision to avoid "recall: what did we decide"
    # being caught by the decision pattern
    (re.compile(r"(?:recall|remember|what did|find|search|look up)[:\s]+(.+)", re.IGNORECASE), Intent.RECALL),
    (re.compile(r"(?:what do (?:we|i) know about|show me|pull up)\s+(.+)", re.IGNORECASE), Intent.RECALL),

    # Decision — anchored to start of utterance to avoid mid-sentence matches
    (re.compile(r"^(?:decide|decision|lock in|we decided|decided)[:\s]+(.+)", re.IGNORECASE), Intent.DECISION),

    # Message / reply  — "message hisham: hey" or "reply to hisham: thanks"
    (re.compile(r"(?:message|reply|respond|text|email|send)\s+(?:to\s+)?(\w+)[:\s]+(.+)", re.IGNORECASE), Intent.MESSAGE_SEND),
    (re.compile(r"(?:tell|ask)\s+(\w+)\s+(?:to|that|about)\s+(.+)", re.IGNORECASE), Intent.MESSAGE_SEND),

    # Vault capture
    (re.compile(r"(?:note|capture|save|log|write down)[:\s]+(.+)", re.IGNORECASE), Intent.VAULT_CAPTURE),

    # System commands
    (re.compile(r"(?:restart|stop|start|status|health)\s+(.+)", re.IGNORECASE), Intent.COMMAND),
]

# ---------------------------------------------------------------------------
# Project resolution patterns
# ---------------------------------------------------------------------------

# Known project names. In a production system this would be loaded from
# the ontology, but for Tier 0 we use a static list that covers the
# operator's active projects.
PROJECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:for|in|on|project)\s+(nuchay|aos|unified-comms|tafsir|qareen|companion)", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Priority extraction
# ---------------------------------------------------------------------------

PRIORITY_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b(?:p1|critical|urgent)\b", re.IGNORECASE), 1),
    (re.compile(r"\b(?:p2|high|important)\b", re.IGNORECASE), 2),
    (re.compile(r"\b(?:p4|low)\b", re.IGNORECASE), 4),
    (re.compile(r"\b(?:p5|someday|maybe)\b", re.IGNORECASE), 5),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(text: str) -> IntentResult:
    """Classify an utterance using Tier 0 regex patterns.

    Args:
        text: Raw operator utterance.

    Returns:
        IntentResult with classified intent, confidence, extracted entities,
        and tier_used=0.
    """
    text_stripped = text.strip()
    if not text_stripped:
        return IntentResult(
            intent=Intent.FILLER,
            confidence=0.5,
            raw_text=text,
            tier_used=0,
        )

    text_lower = text_stripped.lower()

    for pattern, intent in PATTERNS:
        match = pattern.search(text_lower)
        if match:
            entities = _extract_entities(text_stripped, match, intent)
            return IntentResult(
                intent=intent,
                confidence=0.9,
                entities=entities,
                raw_text=text_stripped,
                tier_used=0,
            )

    # No match -- classify as filler (short) or unknown (longer)
    if len(text_stripped.split()) < 3:
        return IntentResult(
            intent=Intent.FILLER,
            confidence=0.5,
            raw_text=text_stripped,
            tier_used=0,
        )

    return IntentResult(
        intent=Intent.UNKNOWN,
        confidence=0.3,
        raw_text=text_stripped,
        tier_used=0,
    )


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entities(
    text: str, match: re.Match[str], intent: Intent
) -> list[ExtractedEntity]:
    """Extract entities from a matched utterance.

    Extracts:
      - project: if a known project name is mentioned
      - topic: the main content from the regex capture group
      - priority: if a priority keyword is present
      - recipient: for message intents

    Args:
        text: Original (non-lowered) utterance.
        match: The regex match object from the intent pattern.
        intent: The classified intent (used to decide extraction strategy).

    Returns:
        List of ExtractedEntity instances.
    """
    entities: list[ExtractedEntity] = []

    # Extract project mentions
    for proj_pattern in PROJECT_PATTERNS:
        proj_match = proj_pattern.search(text)
        if proj_match:
            project_name = proj_match.group(1).lower()
            entities.append(ExtractedEntity(
                entity_type="project",
                value=project_name,
                resolved_id=project_name,
                confidence=0.95,
            ))

    # Extract priority
    for pri_pattern, pri_value in PRIORITY_PATTERNS:
        if pri_pattern.search(text):
            entities.append(ExtractedEntity(
                entity_type="number",
                value=str(pri_value),
                resolved_id=str(pri_value),
                confidence=0.9,
            ))
            break

    # Extract the main content (topic)
    if match.groups():
        if intent == Intent.MESSAGE_SEND and len(match.groups()) >= 2:
            # Message intent: group(1) = recipient, group(2) = message body
            entities.append(ExtractedEntity(
                entity_type="person",
                value=match.group(1),
                confidence=0.8,
            ))
            content = match.group(2).strip()
        else:
            content = match.group(1).strip()

        # Remove project mention from topic content
        for proj_pattern in PROJECT_PATTERNS:
            content = proj_pattern.sub("", content).strip()

        # Remove priority keywords from topic content
        for pri_pattern, _ in PRIORITY_PATTERNS:
            content = pri_pattern.sub("", content).strip()

        # Clean up extra whitespace
        content = re.sub(r"\s+", " ", content).strip()

        if content:
            entities.append(ExtractedEntity(
                entity_type="topic",
                value=content,
                confidence=0.8,
            ))

    return entities
