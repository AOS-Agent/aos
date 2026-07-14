"""Classifiers — rule-based tier + (optional) LLM context tagging.

Two layers, decoupled:

1. RuleClassifier — always runs, deterministic, no LLM, no I/O beyond
   reading a PersonProfile. Produces a Tier. Free, fast, reliable.

2. LLMClassifier — opt-in via the ``--with-llm`` flag (see cli.py).
   Uses the ExecutionRouter to call the operator's preferred execution
   model and produces a list of overlapping context tags from the
   closed vocabulary in ``taxonomy.py``. Budget-gated, cost-tracked.

ClassificationResult always carries a Tier (from rules) and a list of
context tags (empty if rule-only). These are independent layers — the
LLM can be wrong about tags and the tier is still valid.

Privacy: prompt and response bodies are NEVER logged at INFO. Only
model, tokens, duration. Profiles contain real names.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .profiler import PersonProfile
from .taxonomy import (
    CONTEXT_TAGS,
    ClassificationResult,
    Tier,
    new_run_id,
    validate_tags,
)

logger = logging.getLogger(__name__)


# ── Rule thresholds — tunable module constants ───────────────────────
#
# The rule classifier walks a decision tree based on density, recency,
# channel count, and pattern. These thresholds tune the bucketing.
# They are deliberate heuristics — change them in one place without
# touching the tree.

CORE_MAX_DAYS_SINCE = 30
CORE_MIN_DENSITY_RANK = "high"
CORE_MIN_CHANNELS = 3

ACTIVE_MAX_DAYS_SINCE = 90
ACTIVE_MIN_DENSITY_RANK_ANY = ("high", "medium")
ACTIVE_MIN_CHANNELS = 2

CHANNEL_SPECIFIC_MIN_DENSITY_RANK = "high"

EMERGING_MAX_DAYS_SINCE = 90

FADING_MIN_DAYS_SINCE = 180
FADING_PATTERNS = frozenset({"fading", "clustered"})

DORMANT_MIN_DAYS_SINCE = 365


# Ordered ranking of density levels (for >= comparisons).
_RANK_ORDER: dict[str, int] = {
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def _rank_at_least(profile_rank: str, minimum: str) -> bool:
    return _RANK_ORDER.get(profile_rank, 0) >= _RANK_ORDER.get(minimum, 0)


def _rank_in(profile_rank: str, ranks: tuple[str, ...]) -> bool:
    return profile_rank in ranks


# ── Rule classifier ──────────────────────────────────────────────────

class RuleClassifier:
    """Deterministic tier classifier driven by profile metrics.

    Walks the decision tree once per profile. No LLM, no randomness.
    Thread-safe — no instance state is mutated.
    """

    def classify(self, profile: PersonProfile) -> ClassificationResult:
        tier = self._determine_tier(profile)
        return ClassificationResult(
            person_id=profile.person_id,
            tier=tier,
            context_tags=[],
            reasoning="",
            model=None,
            run_id=new_run_id(),
        )

    def _determine_tier(self, profile: PersonProfile) -> Tier:
        # 1. No signals at all → UNKNOWN.
        if (
            profile.density_rank == "minimal"
            and profile.total_messages == 0
            and profile.total_calls == 0
            and profile.total_photos == 0
            and profile.total_emails == 0
            and profile.total_mentions == 0
        ):
            return Tier.UNKNOWN

        days = profile.days_since_last if profile.days_since_last is not None else 10_000

        # 2. DORMANT — extremely old OR essentially no signal density.
        # This is checked BEFORE recent-tier rules because a dormant person
        # might still have a recent stray mention; we still want DORMANT.
        if days >= DORMANT_MIN_DAYS_SINCE:
            return Tier.DORMANT
        if profile.density_rank == "minimal":
            return Tier.DORMANT

        # 3. CORE — multi-channel, high density, very recent, actively messaging.
        #    Requires BOTH all-time reciprocity AND recent outbound activity.
        #    This prevents one-way group-chat contacts and historically-active-
        #    but-now-silent contacts from reaching CORE.
        reciprocity = getattr(profile, "response_reciprocity", 0.5)
        recent_outbound = getattr(profile, "recent_outbound", 0)
        is_reciprocal = reciprocity >= 0.15  # at least 15% of all-time comms are outbound
        has_recent_outbound = recent_outbound > 0  # sent at least 1 message in last 30 days
        if (
            profile.channel_count >= CORE_MIN_CHANNELS
            and _rank_at_least(profile.density_rank, CORE_MIN_DENSITY_RANK)
            and days <= CORE_MAX_DAYS_SINCE
            and is_reciprocal
            and has_recent_outbound
        ):
            return Tier.CORE

        # 4. EMERGING — recent growing pattern OR high burstiness (intense recent engagement).
        burstiness = getattr(profile, "burstiness", None)
        if (
            profile.dominant_pattern == "growing"
            and days <= EMERGING_MAX_DAYS_SINCE
        ):
            return Tier.EMERGING
        if (
            burstiness is not None
            and burstiness > 1.5
            and days <= EMERGING_MAX_DAYS_SINCE
            and profile.recent_volume >= 20
        ):
            return Tier.EMERGING

        # 5. ACTIVE — multi-channel (2+) AND density medium-or-high AND recent activity.
        #    Must have SOME recent communication (recent_volume > 0) to be active.
        #    Without this, people with old multi-channel history and a single
        #    recent message appear "active" when they're really fading.
        if (
            profile.channel_count >= ACTIVE_MIN_CHANNELS
            and _rank_in(profile.density_rank, ACTIVE_MIN_DENSITY_RANK_ANY)
            and days <= ACTIVE_MAX_DAYS_SINCE
            and profile.recent_volume > 0
        ):
            return Tier.ACTIVE

        # 6. CHANNEL_SPECIFIC — single-channel but high density.
        #    If evening_ratio is high, boost toward personal relationship classification.
        evening_ratio = getattr(profile, "evening_ratio", 0.0)
        if (
            profile.channel_count == 1
            and _rank_at_least(profile.density_rank, CHANNEL_SPECIFIC_MIN_DENSITY_RANK)
        ):
            return Tier.CHANNEL_SPECIFIC
        # Single channel, medium density, but primarily personal hours → CHANNEL_SPECIFIC
        if (
            profile.channel_count == 1
            and profile.density_rank == "medium"
            and evening_ratio > 0.5
            and days <= ACTIVE_MAX_DAYS_SINCE
        ):
            return Tier.CHANNEL_SPECIFIC

        # 7. FADING — was active, now trending down OR old-ish with moderate density.
        if (
            profile.dominant_pattern in FADING_PATTERNS
            and days >= FADING_MIN_DAYS_SINCE
        ):
            return Tier.FADING
        if days >= FADING_MIN_DAYS_SINCE and profile.density_rank in ("low", "medium"):
            return Tier.FADING

        # 8. No recent activity = FADING regardless of density.
        #    Someone with medium density from years ago but no recent communication
        #    is fading, not active. The density comes from historical depth.
        if profile.recent_volume == 0:
            return Tier.FADING

        # 9. Low density + old = FADING.
        if profile.density_rank == "low" and days > 60:
            return Tier.FADING

        # 10. Default — has recent volume, moderate density, recent contact → ACTIVE.
        return Tier.ACTIVE


# ── LLM classifier ───────────────────────────────────────────────────

# Prompt template — kept as a module constant so it's easy to inspect
# and tune. {placeholders} are filled at compile time.

PROMPT_TEMPLATE = """You are classifying a person's relationship to an operator based on \
aggregated communication signals. You MUST return a JSON object with \
two fields: `tags` (a list of {{"tag": string, "confidence": float 0.0-1.0}} \
objects) and `reasoning` (one sentence of justification).

Tags MUST come from this allowed vocabulary:
{vocabulary}

A person can carry multiple overlapping tags (family + childhood + close_friend).
Pick only tags strongly supported by the profile below.

Rule-based tier (for context only, do not constrain your tag choices):
{rule_tier}

Profile:
{profile_summary}
{corrections_block}

Return ONLY the JSON object. No preamble, no markdown fence, no commentary.
Example:
{{"tags": [{{"tag": "family_nuclear", "confidence": 0.9}}, {{"tag": "close_friend", "confidence": 0.7}}], "reasoning": "multi-channel recent contact with kinship signals"}}
"""


@dataclass
class LLMClassifierConfig:
    model: str | None = None           # None → use operator preferred
    max_tokens: int = 400
    temperature: float = 0.2
    timeout_seconds: float = 30.0


class LLMClassifier:
    """Compiles a prompt from a profile, calls the ExecutionRouter, parses tags.

    Designed to be mockable in tests — the router is injected via the
    constructor so tests pass a fake.
    """

    def __init__(
        self,
        router: Any | None = None,
        config: LLMClassifierConfig | None = None,
    ) -> None:
        self._router = router
        self.config = config or LLMClassifierConfig()

    # ── Lazy router resolution ──

    def _get_router(self) -> Any:
        """Return a router instance, lazily importing the real one."""
        if self._router is not None:
            return self._router
        try:
            from engine.execution.router import ExecutionRouter
        except ImportError:
            from core.engine.execution.router import ExecutionRouter
        self._router = ExecutionRouter()
        return self._router

    # ── Public classify ──

    async def classify(
        self,
        profile: PersonProfile,
        rule_result: ClassificationResult,
        recent_corrections: list[dict] | None = None,
    ) -> ClassificationResult:
        """Compile prompt, call LLM, parse response, return result.

        On any error (router failure, malformed response), returns a
        ClassificationResult with empty context_tags and the error in
        ``reasoning``. The tier from rule_result is preserved.
        """
        prompt = self._compile_prompt(profile, rule_result, recent_corrections)

        try:
            router = self._get_router()
            response = await router.execute(
                agent_id="intel_classifier",
                prompt=prompt,
                model=self.config.model or "sonnet",
                max_tokens=self.config.max_tokens,
            )
        except Exception as e:
            logger.exception("LLM classifier router call failed")
            return ClassificationResult(
                person_id=profile.person_id,
                tier=rule_result.tier,
                context_tags=[],
                reasoning=f"error: {type(e).__name__}",
                model=self.config.model,
                run_id=new_run_id(),
            )

        # ``response`` may be an ExecutionResult-like object with a .text
        # attribute, or a plain dict, or a string. Handle gracefully.
        text = self._extract_text(response)
        model_name = self._extract_model(response) or self.config.model

        if not text:
            return ClassificationResult(
                person_id=profile.person_id,
                tier=rule_result.tier,
                context_tags=[],
                reasoning="empty response",
                model=model_name,
                run_id=new_run_id(),
            )

        tags, reasoning = self._parse_response(text)
        return ClassificationResult(
            person_id=profile.person_id,
            tier=rule_result.tier,
            context_tags=tags,
            reasoning=reasoning,
            model=model_name,
            run_id=new_run_id(),
        )

    # ── Prompt compilation ──

    def _compile_prompt(
        self,
        profile: PersonProfile,
        rule_result: ClassificationResult,
        recent_corrections: list[dict] | None,
    ) -> str:
        vocabulary_str = ", ".join(sorted(CONTEXT_TAGS))
        profile_summary = self._summarize_profile(profile)
        corrections_block = self._format_corrections(recent_corrections or [])
        return PROMPT_TEMPLATE.format(
            vocabulary=vocabulary_str,
            rule_tier=rule_result.tier.value,
            profile_summary=profile_summary,
            corrections_block=corrections_block,
        )

    @staticmethod
    def _summarize_profile(profile: PersonProfile) -> str:
        lines: list[str] = []
        name = profile.person_name or "(unknown)"
        lines.append(f"- Name: {name}")
        lines.append(
            f"- Sources covered: {', '.join(sorted(profile.source_coverage)) or '(none)'}"
        )
        lines.append(
            f"- Messages: {profile.total_messages} across {profile.channel_count} channels"
        )
        if profile.channels_active:
            lines.append(f"- Active channels: {', '.join(profile.channels_active)}")
        if profile.total_calls:
            lines.append(f"- Calls: {profile.total_calls}")
        if profile.total_photos:
            lines.append(f"- Photos together: {profile.total_photos}")
        if profile.total_emails:
            lines.append(f"- Emails: {profile.total_emails}")
        if profile.total_mentions:
            lines.append(f"- Mentions in operator's notes/tasks: {profile.total_mentions}")
        if profile.days_since_last is not None:
            lines.append(f"- Days since last interaction: {profile.days_since_last}")
        if profile.span_years:
            lines.append(f"- Interaction span: {profile.span_years} years")
        lines.append(f"- Temporal pattern: {profile.dominant_pattern}")
        lines.append(f"- Signal density: {profile.density_rank}")
        if profile.has_birthday:
            lines.append("- Birthday is recorded in contacts")
        if profile.has_physical_address:
            lines.append("- Physical address is recorded")
        if profile.has_related_names:
            lines.append("- Related names are recorded (relational markers)")
        if profile.circles:
            circle_names = ", ".join(
                c.get("name", "") for c in profile.circles[:5] if c.get("name")
            )
            if circle_names:
                lines.append(f"- Detected circles: {circle_names}")
        return "\n".join(lines)

    @staticmethod
    def _format_corrections(corrections: list[dict]) -> str:
        if not corrections:
            return ""
        lines = ["\nRecent operator corrections (treat as ground truth patterns):"]
        for c in corrections[:10]:
            old_tier = c.get("old_tier") or "?"
            new_tier = c.get("new_tier") or "?"
            new_tags = c.get("new_tags") or []
            tag_str = ", ".join(
                t.get("tag", "") if isinstance(t, dict) else str(t)
                for t in new_tags
            )
            notes = (c.get("notes") or "").strip()
            lines.append(
                f"  - Operator re-tiered {old_tier} → {new_tier}"
                + (f" with tags: {tag_str}" if tag_str else "")
                + (f" ({notes})" if notes else "")
            )
        return "\n".join(lines)

    # ── Response parsing ──

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Pull text content out of whatever the router returned."""
        if response is None:
            return ""
        if isinstance(response, str):
            return response
        if hasattr(response, "text"):
            return response.text or ""
        if isinstance(response, dict):
            return response.get("text") or response.get("content") or ""
        return str(response)

    @staticmethod
    def _extract_model(response: Any) -> str | None:
        if response is None:
            return None
        if hasattr(response, "model"):
            return getattr(response, "model", None)
        if isinstance(response, dict):
            return response.get("model")
        return None

    @staticmethod
    def _parse_response(text: str) -> tuple[list[dict], str]:
        """Extract tags + reasoning from LLM output.

        Robustness:
          - Strip markdown code fences (```json ... ```)
          - Use json.loads with fallback
          - Validate against CONTEXT_TAGS via taxonomy.validate_tags
          - On any parse failure, return ([], raw_text_trimmed)
        """
        if not text:
            return [], ""

        # Strip markdown fences.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)

        # Try to find the first JSON object in the text.
        # Greedy find of the outermost {...}.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return [], cleaned[:200]

        json_text = match.group(0)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            return [], cleaned[:200]

        if not isinstance(parsed, dict):
            return [], cleaned[:200]

        raw_tags = parsed.get("tags") or []
        reasoning = parsed.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        tags = validate_tags(raw_tags)
        return tags, reasoning.strip()[:500]
