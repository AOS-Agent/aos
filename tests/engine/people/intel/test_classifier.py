"""Tests for the rule classifier + LLM classifier.

The LLM classifier is tested with a mock router — never touches a real
model. All test profiles are fabricated.
"""
import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine.people.intel.classifier import (
    LLMClassifier,
    LLMClassifierConfig,
    RuleClassifier,
)
from core.engine.people.intel.profiler import PersonProfile
from core.engine.people.intel.taxonomy import (
    ClassificationResult,
    Tier,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _profile(**kwargs) -> PersonProfile:
    """Build a fabricated PersonProfile with sensible defaults."""
    defaults = {
        "person_id": "p_test",
        "person_name": "Test Person",
        "source_coverage": ["test"],
        "total_messages": 0,
        "total_calls": 0,
        "total_photos": 0,
        "total_emails": 0,
        "total_mentions": 0,
        "channels_active": [],
        "channel_count": 0,
        "is_multi_channel": False,
        "days_since_last": None,
        "span_years": 0.0,
        "density_score": 0.0,
        "density_rank": "minimal",
        "dominant_pattern": "none",
        "circles": [],
    }
    defaults.update(kwargs)
    return PersonProfile(**defaults)


# ── Rule classifier ──────────────────────────────────────────────────


def test_rule_unknown_for_empty_profile():
    classifier = RuleClassifier()
    result = classifier.classify(_profile())
    assert result.tier == Tier.UNKNOWN
    assert result.context_tags == []
    assert result.model is None


def test_rule_dormant_for_very_old_contact():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=50,
            channel_count=2,
            density_rank="medium",
            days_since_last=500,
        )
    )
    assert result.tier == Tier.DORMANT


def test_rule_dormant_for_minimal_density():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=1,
            channel_count=1,
            density_rank="minimal",
            days_since_last=10,
        )
    )
    assert result.tier == Tier.DORMANT


def test_rule_core_requires_all_conditions():
    classifier = RuleClassifier()
    # All three conditions: multi-channel + high density + recent
    result = classifier.classify(
        _profile(
            channel_count=4,
            density_rank="high",
            days_since_last=15,
            total_messages=800,
        )
    )
    assert result.tier == Tier.CORE


def test_rule_not_core_when_density_insufficient():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            channel_count=4,
            density_rank="medium",  # not high
            days_since_last=15,
            total_messages=200,
        )
    )
    assert result.tier != Tier.CORE


def test_rule_not_core_when_not_recent():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            channel_count=4,
            density_rank="high",
            days_since_last=60,  # > 30
            total_messages=500,
        )
    )
    assert result.tier != Tier.CORE


def test_rule_emerging_for_growing_pattern():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=30,
            channel_count=1,
            density_rank="low",
            days_since_last=20,
            dominant_pattern="growing",
        )
    )
    assert result.tier == Tier.EMERGING


def test_rule_active_multi_channel_medium_density():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=200,
            channel_count=2,
            density_rank="medium",
            days_since_last=40,
        )
    )
    assert result.tier == Tier.ACTIVE


def test_rule_channel_specific_single_channel_high_density():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=800,
            channel_count=1,
            density_rank="high",
            days_since_last=10,
        )
    )
    assert result.tier == Tier.CHANNEL_SPECIFIC


def test_rule_fading_for_fading_pattern():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=100,
            channel_count=1,
            density_rank="low",
            days_since_last=250,
            dominant_pattern="fading",
        )
    )
    assert result.tier == Tier.FADING


def test_rule_fading_for_old_with_moderate_density():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=50,
            channel_count=2,
            density_rank="low",
            days_since_last=200,
            dominant_pattern="episodic",
        )
    )
    # Old + low density + not yet dormant (< 365) + pattern not fading
    # → FADING via the "old + low/medium density" rule
    assert result.tier == Tier.FADING


def test_rule_default_to_active_for_moderate_recent():
    classifier = RuleClassifier()
    result = classifier.classify(
        _profile(
            total_messages=30,
            channel_count=1,
            density_rank="low",
            days_since_last=60,
            dominant_pattern="episodic",
        )
    )
    # Doesn't hit CORE, EMERGING, FADING, DORMANT, CHANNEL_SPECIFIC
    # Falls through to default ACTIVE
    assert result.tier == Tier.ACTIVE


def test_rule_result_populates_run_id_and_timestamp():
    classifier = RuleClassifier()
    result = classifier.classify(_profile(total_messages=5, density_rank="low"))
    assert result.run_id.startswith("run_")
    assert result.created_at  # ISO timestamp set


def test_rule_core_precedes_channel_specific():
    """If a profile qualifies for both, CORE should win."""
    classifier = RuleClassifier()
    # Hypothetically a profile could match both — but CORE requires
    # channels>=3, so CHANNEL_SPECIFIC (channels==1) can't collide. This
    # test locks that invariant in.
    result = classifier.classify(
        _profile(
            channel_count=3,
            density_rank="high",
            days_since_last=10,
            total_messages=500,
        )
    )
    assert result.tier == Tier.CORE


# ── LLM classifier ───────────────────────────────────────────────────


@pytest.fixture()
def mock_router():
    router = MagicMock()
    router.execute = AsyncMock()
    return router


def _rule_result(tier: Tier = Tier.ACTIVE) -> ClassificationResult:
    return ClassificationResult(person_id="p_test", tier=tier)


def _run_async(coro):
    return asyncio.run(coro)


def test_llm_compile_prompt_includes_profile_fields(mock_router):
    llm = LLMClassifier(router=mock_router)
    profile = _profile(
        person_name="Fabricated Person",
        total_messages=100,
        channel_count=3,
        density_rank="high",
        days_since_last=15,
        dominant_pattern="consistent",
    )
    prompt = llm._compile_prompt(profile, _rule_result(Tier.CORE), None)
    assert "Fabricated Person" in prompt
    assert "100" in prompt
    assert "consistent" in prompt
    assert "core" in prompt.lower()


def test_llm_compile_prompt_lists_vocabulary(mock_router):
    llm = LLMClassifier(router=mock_router)
    prompt = llm._compile_prompt(_profile(), _rule_result(), None)
    assert "family_nuclear" in prompt
    assert "close_friend" in prompt


def test_llm_compile_prompt_includes_corrections(mock_router):
    llm = LLMClassifier(router=mock_router)
    corrections = [
        {
            "old_tier": "active",
            "new_tier": "core",
            "new_tags": [{"tag": "family_nuclear", "confidence": 1.0}],
            "notes": "immediate family",
        }
    ]
    prompt = llm._compile_prompt(_profile(), _rule_result(), corrections)
    assert "Recent operator corrections" in prompt
    assert "active" in prompt
    assert "core" in prompt


def test_llm_parse_clean_json():
    tags, reasoning = LLMClassifier._parse_response(
        '{"tags": [{"tag": "family_nuclear", "confidence": 0.9}], "reasoning": "kin"}'
    )
    assert len(tags) == 1
    assert tags[0]["tag"] == "family_nuclear"
    assert reasoning == "kin"


def test_llm_parse_markdown_fenced_json():
    tags, reasoning = LLMClassifier._parse_response(
        '```json\n{"tags": [{"tag": "close_friend", "confidence": 0.8}], "reasoning": "ok"}\n```'
    )
    assert len(tags) == 1
    assert tags[0]["tag"] == "close_friend"


def test_llm_parse_malformed_json_returns_empty_tags():
    tags, reasoning = LLMClassifier._parse_response(
        "This is not JSON at all"
    )
    assert tags == []
    # Raw text surfaced in reasoning for debugging
    assert "not JSON" in reasoning


def test_llm_parse_invalid_json_syntax():
    tags, reasoning = LLMClassifier._parse_response(
        '{"tags": [{"tag": "family_nuclear"',  # truncated
    )
    assert tags == []


def test_llm_parse_drops_unknown_tags():
    tags, _ = LLMClassifier._parse_response(
        '{"tags": [{"tag": "family_nuclear", "confidence": 0.9}, '
        '{"tag": "bogus_tag", "confidence": 0.5}], "reasoning": "ok"}'
    )
    assert len(tags) == 1
    assert tags[0]["tag"] == "family_nuclear"


def test_llm_parse_extracts_json_from_preamble():
    tags, _ = LLMClassifier._parse_response(
        'Here is the classification:\n{"tags": [{"tag": "colleague", "confidence": 0.7}], "reasoning": "work"}'
    )
    assert len(tags) == 1
    assert tags[0]["tag"] == "colleague"


def test_llm_classify_success(mock_router):
    """Full classify() path with a mocked router returning valid JSON."""
    llm = LLMClassifier(router=mock_router)
    mock_response = MagicMock()
    mock_response.text = (
        '{"tags": [{"tag": "family_nuclear", "confidence": 0.95}], '
        '"reasoning": "test reason"}'
    )
    mock_response.model = "test-model"
    mock_router.execute.return_value = mock_response

    result = _run_async(
        llm.classify(_profile(person_name="Test"), _rule_result(Tier.CORE))
    )
    assert result.tier == Tier.CORE  # preserved from rule_result
    assert len(result.context_tags) == 1
    assert result.context_tags[0]["tag"] == "family_nuclear"
    assert result.reasoning == "test reason"
    assert result.model == "test-model"
    mock_router.execute.assert_called_once()


def test_llm_classify_router_error_returns_empty_tags(mock_router):
    """If the router raises, we get empty tags + error reasoning."""
    llm = LLMClassifier(router=mock_router)
    mock_router.execute.side_effect = RuntimeError("connection refused")

    result = _run_async(llm.classify(_profile(), _rule_result(Tier.ACTIVE)))
    assert result.tier == Tier.ACTIVE  # preserved
    assert result.context_tags == []
    assert "RuntimeError" in result.reasoning or "error" in result.reasoning.lower()


def test_llm_classify_empty_response(mock_router):
    llm = LLMClassifier(router=mock_router)
    mock_response = MagicMock()
    mock_response.text = ""
    mock_router.execute.return_value = mock_response

    result = _run_async(llm.classify(_profile(), _rule_result()))
    assert result.context_tags == []
    assert "empty" in result.reasoning.lower()


def test_llm_classify_string_response_accepted(mock_router):
    """Router returning a bare string should still work."""
    llm = LLMClassifier(router=mock_router)
    mock_router.execute.return_value = (
        '{"tags": [{"tag": "friend", "confidence": 0.6}], "reasoning": "r"}'
    )
    result = _run_async(llm.classify(_profile(), _rule_result()))
    assert len(result.context_tags) == 1
    assert result.context_tags[0]["tag"] == "friend"
