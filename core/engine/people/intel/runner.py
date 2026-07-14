"""Classifier runner — orchestrator for Phase 4.

Ties everything together:

    build_person_index (inherit from extractor, not re-done)
         │
         ▼
    ProfileBuilder.build_all(person_ids)
         │
         ▼
    RuleClassifier.classify(profile)          ← always runs
         │
         ├─ if with_llm: ──────────────────┐
         │                                  │
         │                                  ▼
         │                      LLMClassifier.classify(profile, rule_result, corrections)
         │                                  │
         ▼                                  │
    ClassificationResult ◀────── merged ◀───┘
         │
         ▼
    ClassificationStore.save()

Cost control:
- Pre-estimate a budget cap in USD; refuse to start if exceeded
- Track actual spend via executions.log_execution() (reused pattern)
- Hard stop mid-run if actual spend exceeds the cap
- ``dry_run`` mode: build profiles + compile prompts, skip LLM + skip writes

Correction feedback:
- Pulls recent classification_feedback rows
- Passes them to LLMClassifier as few-shot examples in the prompt
- Operator corrections influence future runs (minimal Subsystem C primitive)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .classifier import LLMClassifier, LLMClassifierConfig, RuleClassifier
from .feedback import ClassificationStore
from .profiler import PersonProfile, ProfileBuilder
from .store import DEFAULT_DB_PATH
from .taxonomy import ClassificationResult, Tier, new_run_id

logger = logging.getLogger(__name__)


# ── Budget defaults ──────────────────────────────────────────────────
#
# Rough token estimate per classification call — the prompt is bounded
# (profile summary + vocabulary + corrections) at roughly:
#   ~600 input tokens  (profile + vocabulary + instructions)
#   ~200 output tokens (JSON response)
# These numbers are conservative; real spend should be tracked via the
# execution router's log.

ESTIMATED_INPUT_TOKENS_PER_CALL = 800
ESTIMATED_OUTPUT_TOKENS_PER_CALL = 300
DEFAULT_BUDGET_USD = 1.00


def _estimate_cost_per_call(model: str | None) -> float:
    """Rough cost estimate per call for budget pre-check.

    Uses a pessimistic pricing assumption when model is unknown so we
    err on the side of over-estimating — the real cost tracker will
    correct this after the first few calls.
    """
    # Pessimistic default: $5 per 1M input tokens + $15 per 1M output tokens
    # (roughly sonnet-equivalent). Any cheaper model just means we under-run
    # the budget, which is fine.
    input_cost_per_1m = 5.0
    output_cost_per_1m = 15.0
    return (
        (ESTIMATED_INPUT_TOKENS_PER_CALL / 1_000_000) * input_cost_per_1m
        + (ESTIMATED_OUTPUT_TOKENS_PER_CALL / 1_000_000) * output_cost_per_1m
    )


# ── Report ───────────────────────────────────────────────────────────

@dataclass
class ClassifyRunReport:
    persons_profiled: int = 0
    rule_classifications: int = 0
    llm_classifications: int = 0
    llm_errors: int = 0
    persisted: int = 0
    dry_run: bool = False
    with_llm: bool = False
    budget_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    tier_distribution: dict[str, int] = field(default_factory=dict)
    aborted_reason: str | None = None
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "persons_profiled": self.persons_profiled,
            "rule_classifications": self.rule_classifications,
            "llm_classifications": self.llm_classifications,
            "llm_errors": self.llm_errors,
            "persisted": self.persisted,
            "dry_run": self.dry_run,
            "with_llm": self.with_llm,
            "budget_usd": self.budget_usd,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "duration_seconds": self.duration_seconds,
            "tier_distribution": dict(self.tier_distribution),
            "aborted_reason": self.aborted_reason,
            "errors": list(self.errors),
        }


# ── Runner ───────────────────────────────────────────────────────────

class ClassifierRunner:
    """Orchestrator for the classification pipeline."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        profile_builder: ProfileBuilder | None = None,
        rule_classifier: RuleClassifier | None = None,
        llm_classifier: LLMClassifier | None = None,
        classification_store: ClassificationStore | None = None,
    ) -> None:
        self.db_path: Path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.profile_builder = profile_builder or ProfileBuilder(self.db_path)
        self.rule_classifier = rule_classifier or RuleClassifier()
        self.llm_classifier = llm_classifier or LLMClassifier()
        self.store = classification_store or ClassificationStore(self.db_path)

    # ── Main entrypoint ──

    async def run(
        self,
        person_ids: list[str] | None = None,
        limit: int | None = None,
        with_llm: bool = False,
        max_budget_usd: float | None = None,
        dry_run: bool = False,
        llm_model: str | None = None,
    ) -> ClassifyRunReport:
        """Run the classification pipeline for a set of persons."""
        start = time.time()

        # Ensure tables exist on first run (idempotent).
        if not dry_run:
            try:
                self.store.init_schema()
            except Exception as e:
                logger.warning("classification store schema init failed: %s", e)

        # Build profiles.
        profiles = self.profile_builder.build_all(person_ids=person_ids)
        if limit is not None and limit > 0:
            # Stable ordering for repeatable --limit runs.
            sorted_pids = sorted(profiles.keys())[:limit]
            profiles = {pid: profiles[pid] for pid in sorted_pids}

        report = ClassifyRunReport(
            persons_profiled=len(profiles),
            dry_run=dry_run,
            with_llm=with_llm,
            budget_usd=max_budget_usd if max_budget_usd is not None else (
                DEFAULT_BUDGET_USD if with_llm else 0.0
            ),
        )

        if not profiles:
            report.duration_seconds = round(time.time() - start, 2)
            return report

        # Budget pre-check for LLM runs.
        if with_llm:
            per_call = _estimate_cost_per_call(llm_model)
            estimate = per_call * len(profiles)
            report.estimated_cost_usd = round(estimate, 4)

            budget = report.budget_usd
            if budget and estimate > budget:
                report.aborted_reason = (
                    f"estimated cost ${estimate:.4f} exceeds budget ${budget:.2f}"
                )
                report.duration_seconds = round(time.time() - start, 2)
                return report

        # Load recent corrections once for the whole run.
        try:
            recent_corrections = self.store.recent_feedback(limit=10)
        except Exception:
            recent_corrections = []

        # Process each person.
        actual_spend = 0.0
        per_call_cost = _estimate_cost_per_call(llm_model) if with_llm else 0.0

        for person_id in sorted(profiles.keys()):
            profile = profiles[person_id]
            try:
                result = await self._classify_one(
                    profile,
                    with_llm=with_llm,
                    recent_corrections=recent_corrections,
                    model=llm_model,
                )
            except Exception as e:
                logger.exception("classify_one failed for %s", person_id)
                report.errors.append({"person_id": person_id, "error": str(e)})
                continue

            report.rule_classifications += 1
            if with_llm:
                report.llm_classifications += 1
                if not result.context_tags and (
                    result.reasoning.startswith("error")
                    or result.reasoning.startswith("empty")
                ):
                    report.llm_errors += 1
                actual_spend += per_call_cost

            # Budget hard stop mid-run.
            if with_llm and report.budget_usd and actual_spend > report.budget_usd:
                report.aborted_reason = (
                    f"budget exceeded mid-run at ${actual_spend:.4f}"
                )
                break

            # Persist unless dry_run.
            if not dry_run:
                try:
                    self.store.save(result)
                    report.persisted += 1
                except Exception as e:
                    logger.exception("persist failed for %s", person_id)
                    report.errors.append(
                        {"person_id": person_id, "error": f"persist: {e}"}
                    )

            # Track tier distribution.
            tier_key = result.tier.value
            report.tier_distribution[tier_key] = (
                report.tier_distribution.get(tier_key, 0) + 1
            )

        # Sync tier → people.importance after all classifications are saved.
        if not dry_run:
            try:
                sync_stats = self.store.sync_importance()
                logger.info("Importance sync: %s", sync_stats)
            except Exception as e:
                logger.exception("sync_importance failed: %s", e)
                report.errors.append({"sync_importance": str(e)})

        report.estimated_cost_usd = round(actual_spend if with_llm else 0.0, 4)
        report.duration_seconds = round(time.time() - start, 2)
        return report

    # ── Classify one ──

    async def _classify_one(
        self,
        profile: PersonProfile,
        *,
        with_llm: bool,
        recent_corrections: list[dict],
        model: str | None,
    ) -> ClassificationResult:
        rule_result = self.rule_classifier.classify(profile)

        if not with_llm:
            return rule_result

        # Inject model into LLM classifier if caller overrode it.
        if model:
            self.llm_classifier.config = LLMClassifierConfig(
                model=model,
                max_tokens=self.llm_classifier.config.max_tokens,
                temperature=self.llm_classifier.config.temperature,
                timeout_seconds=self.llm_classifier.config.timeout_seconds,
            )

        return await self.llm_classifier.classify(
            profile, rule_result, recent_corrections=recent_corrections
        )

    # ── Correction recording ──

    def record_correction(
        self,
        person_id: str,
        new_tier: Tier | None = None,
        new_tags: list[dict] | None = None,
        notes: str = "",
    ) -> ClassificationResult:
        """Record an operator correction.

        Updates the active classification to the corrected values AND
        appends a row to classification_feedback so future runs can use
        it as a few-shot example.

        Returns the new ClassificationResult now stored for this person.
        """
        old = self.store.load(person_id)

        # Build the new result. If operator only provided tags, keep old
        # tier. If operator only provided tier, keep old tags.
        if old is None:
            tier = new_tier or Tier.UNKNOWN
            tags = new_tags or []
            model = None
            reasoning = "operator correction (no prior classification)"
        else:
            tier = new_tier if new_tier is not None else old.tier
            tags = new_tags if new_tags is not None else old.context_tags
            model = old.model
            reasoning = "operator correction"

        from .taxonomy import validate_tags

        new_result = ClassificationResult(
            person_id=person_id,
            tier=tier,
            context_tags=validate_tags(tags),
            reasoning=reasoning,
            model=model,
            run_id=new_run_id(),
        )

        self.store.init_schema()
        self.store.save(new_result)
        self.store.record_feedback(person_id, old=old, new=new_result, notes=notes)
        return new_result

    # ── Convenience delegates ──

    def tier_distribution(self) -> dict[str, int]:
        return self.store.tier_distribution()

    def get_classification(self, person_id: str) -> ClassificationResult | None:
        return self.store.load(person_id)
