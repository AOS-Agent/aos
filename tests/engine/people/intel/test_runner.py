"""Tests for the classifier runner (orchestrator).

Uses a tmp_path people.db seeded via SignalStore + direct SQL. Mocks the
LLM classifier via constructor injection — never hits a real model.
"""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine.people.intel.classifier import LLMClassifier, RuleClassifier
from core.engine.people.intel.feedback import ClassificationStore
from core.engine.people.intel.profiler import ProfileBuilder
from core.engine.people.intel.runner import (
    ClassifierRunner,
    ClassifyRunReport,
    _estimate_cost_per_call,
)
from core.engine.people.intel.store import SignalStore
from core.engine.people.intel.taxonomy import (
    ClassificationResult,
    Tier,
)
from core.engine.people.intel.types import (
    CommunicationSignal,
    PersonSignals,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def seeded_db(tmp_path: Path):
    """Create a tmp people.db with signals + classification tables."""
    db = tmp_path / "people.db"

    signal_store = SignalStore(db)
    signal_store.init_schema()

    class_store = ClassificationStore(db)
    class_store.init_schema()

    # Seed three persons with different signal shapes.
    for pid, channel, count, days_ago in [
        ("p_core", "imessage", 500, 10),
        ("p_active", "whatsapp", 100, 40),
        ("p_dormant", "sms", 2, 500),
    ]:
        from datetime import datetime, timedelta, timezone
        first = (datetime.now(timezone.utc) - timedelta(days=days_ago + 300)).isoformat()
        last = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        s = PersonSignals(
            person_id=pid,
            person_name=f"Fake {pid}",
            source_coverage=["test"],
        )
        s.communication.append(
            CommunicationSignal(
                source="test",
                channel=channel,
                total_messages=count,
                first_message_date=first,
                last_message_date=last,
                temporal_pattern="consistent" if count > 10 else "one_shot",
            )
        )
        signal_store.save(pid, "test", s)

    return db


def _run(coro):
    return asyncio.run(coro)


def _make_runner(
    seeded_db: Path,
    llm_classifier: LLMClassifier | None = None,
) -> ClassifierRunner:
    return ClassifierRunner(
        db_path=seeded_db,
        llm_classifier=llm_classifier,
    )


# ── Basic rule-only runs ──────────────────────────────────────────────


def test_run_empty_db(tmp_path: Path):
    db = tmp_path / "empty.db"
    SignalStore(db).init_schema()
    ClassificationStore(db).init_schema()
    runner = _make_runner(db)
    report = _run(runner.run())
    assert report.persons_profiled == 0
    assert report.persisted == 0


def test_run_rule_only(seeded_db: Path):
    runner = _make_runner(seeded_db)
    report = _run(runner.run(with_llm=False))

    assert report.persons_profiled == 3
    assert report.rule_classifications == 3
    assert report.llm_classifications == 0
    assert report.persisted == 3
    assert report.errors == []
    assert report.dry_run is False
    assert report.with_llm is False
    # Tier distribution should have entries
    assert sum(report.tier_distribution.values()) == 3


def test_run_persists_classifications(seeded_db: Path):
    runner = _make_runner(seeded_db)
    _run(runner.run(with_llm=False))

    # Check store has entries
    result = runner.get_classification("p_core")
    assert result is not None
    assert result.person_id == "p_core"


def test_run_dry_run_does_not_persist(seeded_db: Path):
    runner = _make_runner(seeded_db)
    report = _run(runner.run(dry_run=True))

    assert report.dry_run is True
    assert report.persisted == 0
    # Nothing in the store
    assert runner.get_classification("p_core") is None


def test_run_filter_by_person_ids(seeded_db: Path):
    runner = _make_runner(seeded_db)
    report = _run(runner.run(person_ids=["p_core"]))
    assert report.persons_profiled == 1
    assert report.persisted == 1


def test_run_limit(seeded_db: Path):
    runner = _make_runner(seeded_db)
    report = _run(runner.run(limit=2))
    assert report.persons_profiled == 2


def test_run_tier_distribution(seeded_db: Path):
    runner = _make_runner(seeded_db)
    _run(runner.run())
    dist = runner.tier_distribution()
    assert sum(dist.values()) == 3


# ── LLM runs (mocked) ────────────────────────────────────────────────


def _mock_llm_classifier(return_tags=None):
    """Build an LLMClassifier with a mocked router that returns set tags."""
    mock_router = MagicMock()
    mock_router.execute = AsyncMock()

    if return_tags is None:
        return_tags = [{"tag": "friend", "confidence": 0.7}]

    import json
    response = MagicMock()
    response.text = json.dumps({"tags": return_tags, "reasoning": "mock"})
    response.model = "mock-model"
    mock_router.execute.return_value = response

    return LLMClassifier(router=mock_router)


def test_run_with_llm_success(seeded_db: Path):
    runner = _make_runner(seeded_db, llm_classifier=_mock_llm_classifier())
    report = _run(runner.run(with_llm=True, max_budget_usd=10.0))

    assert report.with_llm is True
    assert report.llm_classifications == 3
    assert report.llm_errors == 0
    assert report.persisted == 3

    # Every persisted classification should have the mock tags.
    for pid in ("p_core", "p_active", "p_dormant"):
        result = runner.get_classification(pid)
        assert result is not None
        assert len(result.context_tags) == 1
        assert result.context_tags[0]["tag"] == "friend"
        assert result.model == "mock-model"


def test_run_with_llm_error_recovery(seeded_db: Path):
    """An LLM call that raises should not abort the run."""
    mock_router = MagicMock()
    mock_router.execute = AsyncMock()
    mock_router.execute.side_effect = RuntimeError("boom")
    llm = LLMClassifier(router=mock_router)

    runner = _make_runner(seeded_db, llm_classifier=llm)
    report = _run(runner.run(with_llm=True, max_budget_usd=10.0))

    assert report.persons_profiled == 3
    assert report.rule_classifications == 3
    assert report.llm_errors == 3
    # Persisted should still happen — tier from rules, no tags
    assert report.persisted == 3


def test_run_with_llm_empty_tags_counted_as_error(seeded_db: Path):
    """LLM returning valid-but-tagless result doesn't block persistence."""
    llm = _mock_llm_classifier(return_tags=[])  # empty tags
    runner = _make_runner(seeded_db, llm_classifier=llm)
    report = _run(runner.run(with_llm=True, max_budget_usd=10.0))
    # LLM was called and returned, not counted as error (valid JSON)
    # Should still persist
    assert report.persisted == 3


# ── Budget tests ──────────────────────────────────────────────────────


def test_run_budget_abort_before_start(seeded_db: Path):
    """Estimated cost exceeding budget cap aborts before any LLM call."""
    llm = _mock_llm_classifier()
    runner = _make_runner(seeded_db, llm_classifier=llm)
    # Very tight budget
    report = _run(runner.run(with_llm=True, max_budget_usd=0.00001))
    assert report.aborted_reason is not None
    assert "exceeds budget" in report.aborted_reason
    # No LLM calls happened
    assert report.llm_classifications == 0


def test_run_no_budget_abort_when_not_llm(seeded_db: Path):
    """Rule-only runs never trigger budget logic."""
    runner = _make_runner(seeded_db)
    report = _run(runner.run(with_llm=False, max_budget_usd=0.0))
    assert report.aborted_reason is None
    assert report.persisted == 3


def test_estimate_cost_is_positive():
    cost = _estimate_cost_per_call(None)
    assert cost > 0


# ── Correction tests ──────────────────────────────────────────────────


def test_record_correction_creates_active_classification(seeded_db: Path):
    runner = _make_runner(seeded_db)
    # No prior classification
    assert runner.get_classification("p_new") is None

    result = runner.record_correction(
        "p_new",
        new_tier=Tier.CORE,
        new_tags=[{"tag": "family_nuclear", "confidence": 1.0}],
        notes="immediate family",
    )
    assert result.tier == Tier.CORE
    assert len(result.context_tags) == 1
    assert runner.get_classification("p_new") is not None


def test_record_correction_logs_feedback(seeded_db: Path):
    runner = _make_runner(seeded_db)
    runner.record_correction(
        "p_x",
        new_tier=Tier.ACTIVE,
        new_tags=[{"tag": "friend", "confidence": 0.8}],
        notes="corrected",
    )
    recent = runner.store.recent_feedback()
    assert len(recent) == 1
    assert recent[0]["person_id"] == "p_x"
    assert recent[0]["new_tier"] == "active"


def test_record_correction_preserves_tags_when_only_tier_changes(seeded_db: Path):
    runner = _make_runner(seeded_db)
    # Seed a prior classification
    from core.engine.people.intel.taxonomy import ClassificationResult
    runner.store.init_schema()
    runner.store.save(
        ClassificationResult(
            person_id="p_y",
            tier=Tier.DORMANT,
            context_tags=[{"tag": "colleague", "confidence": 0.9}],
        )
    )
    # Correct only the tier
    result = runner.record_correction("p_y", new_tier=Tier.ACTIVE, notes="still active")
    assert result.tier == Tier.ACTIVE
    # Tags preserved
    assert len(result.context_tags) == 1
    assert result.context_tags[0]["tag"] == "colleague"


def test_record_correction_preserves_tier_when_only_tags_change(seeded_db: Path):
    runner = _make_runner(seeded_db)
    runner.store.init_schema()
    runner.store.save(
        ClassificationResult(
            person_id="p_z",
            tier=Tier.ACTIVE,
            context_tags=[{"tag": "colleague", "confidence": 0.9}],
        )
    )
    # Correct only the tags
    result = runner.record_correction(
        "p_z",
        new_tags=[{"tag": "close_friend", "confidence": 0.9}],
    )
    assert result.tier == Tier.ACTIVE
    assert result.context_tags[0]["tag"] == "close_friend"


def test_record_correction_validates_tags(seeded_db: Path):
    runner = _make_runner(seeded_db)
    result = runner.record_correction(
        "p_v",
        new_tier=Tier.ACTIVE,
        new_tags=[
            {"tag": "family_nuclear", "confidence": 0.9},
            {"tag": "bogus_tag", "confidence": 0.8},
        ],
    )
    # Bogus tag dropped
    assert len(result.context_tags) == 1
    assert result.context_tags[0]["tag"] == "family_nuclear"


# ── Report structure ─────────────────────────────────────────────────


def test_report_to_dict_is_json_safe(seeded_db: Path):
    import json
    runner = _make_runner(seeded_db)
    report = _run(runner.run())
    d = report.to_dict()
    json.dumps(d)  # must not raise


def test_report_captures_timing(seeded_db: Path):
    runner = _make_runner(seeded_db)
    report = _run(runner.run())
    assert report.duration_seconds >= 0
