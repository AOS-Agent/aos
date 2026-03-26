"""Auto-eligibility rules for Level 3 autonomous messaging.

HARD GUARDRAILS — these are intentionally NOT configurable via trust.yaml.
Changing what's auto-eligible requires a code change. This is the right
constraint: autonomous messaging is the highest-risk capability.

A message is auto-eligible ONLY if ALL of these are true:
1. Person is at Trust Level 3
2. Message matches an auto-eligible pattern (confirmation, scheduling, simple Q&A)
3. Message does NOT contain any blocking patterns (financial, legal, new contact)
4. Draft confidence >= 0.85

If any check fails, the message falls back to Level 2 (draft for review).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EligibilityResult:
    eligible: bool
    reason: str
    template_key: str = ""   # Which pattern matched


# ── Eligible patterns (message must match at least one) ──

ELIGIBLE_PATTERNS = {
    "confirmation": [
        r"(?i)(sounds good|confirmed?|that works|perfect|great|okay|ok)\??$",
        r"(?i)^(yes|yeah|yep|yup|sure|absolutely|of course|inshallah|inshaAllah)",
        r"(?i)(can you confirm|are you free|does that work|sound good)\??",
    ],
    "scheduling": [
        r"(?i)(what time|when works|are you (free|available)|can we (do|meet|talk))",
        r"(?i)(tomorrow|tonight|this (morning|afternoon|evening|weekend))",
        r"(?i)(let('s| us) (do|meet|talk|catch up))",
    ],
    "greeting": [
        r"(?i)^(assalam|salam|hi|hey|hello|good morning|good evening)",
        r"(?i)^(how are you|how('s| is) it going|what('s| is) up)",
    ],
    "acknowledgment": [
        r"(?i)^(thanks|thank you|jazak|barakAllah|shukran|appreciated)",
        r"(?i)^(got it|noted|understood|will do|on it)",
    ],
}

# ── Blocking patterns (message must NOT match any) ───────

BLOCKING_PATTERNS = [
    r"(?i)\$\d+",                          # Dollar amounts
    r"(?i)\d+\s*(usd|dollars?|euros?|gbp)", # Currency mentions
    r"(?i)(invoice|payment|transfer|wire|send money|iban|routing)",
    r"(?i)(contract|agreement|terms|legal|lawyer|court)",
    r"(?i)(password|login|credentials|api.?key|secret|token)",
    r"(?i)(sorry for your loss|passed away|died|funeral|janazah)",
    r"(?i)(angry|upset|disappointed|frustrated|furious)",
    r"(?i)(urgent|emergency|asap|right now|immediately)",
]


def is_auto_eligible(
    message_text: str,
    person_id: str,
    trust_level: int,
    is_first_contact: bool = False,
) -> EligibilityResult:
    """Check if a message is eligible for autonomous handling.

    Args:
        message_text: The inbound message to evaluate
        person_id: Who sent it
        trust_level: Current comms trust level for this person
        is_first_contact: True if this is the first-ever message from them

    Returns:
        EligibilityResult with eligible flag and reason
    """
    # Gate 1: Must be Level 3
    if trust_level < 3:
        return EligibilityResult(False, f"Trust level {trust_level} < 3")

    # Gate 2: Never auto-handle first contact
    if is_first_contact:
        return EligibilityResult(False, "First-ever message from this person")

    # Gate 3: Check blocking patterns
    for pattern in BLOCKING_PATTERNS:
        if re.search(pattern, message_text):
            return EligibilityResult(False, f"Blocked: matches safety pattern")

    # Gate 4: Must match at least one eligible pattern
    for template_key, patterns in ELIGIBLE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, message_text):
                return EligibilityResult(
                    True,
                    f"Eligible: matches '{template_key}' pattern",
                    template_key=template_key,
                )

    # No eligible pattern matched
    return EligibilityResult(False, "No auto-eligible pattern matched")
