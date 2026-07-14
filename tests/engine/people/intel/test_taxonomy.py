"""Tests for the classification taxonomy."""
from core.engine.people.intel.taxonomy import (
    CONTEXT_TAGS,
    ClassificationResult,
    Tier,
    new_run_id,
    pretty_tier,
    validate_tags,
)


# ── Vocabulary ────────────────────────────────────────────────────────


def test_vocabulary_nonempty():
    assert len(CONTEXT_TAGS) > 10
    assert isinstance(CONTEXT_TAGS, frozenset)


def test_vocabulary_all_lowercase_snake_case():
    for tag in CONTEXT_TAGS:
        assert tag == tag.lower()
        assert " " not in tag
        assert tag.replace("_", "").isalpha()


def test_vocabulary_includes_expected_tags():
    # Spot-check key tags exist
    for expected in [
        "family_nuclear",
        "close_friend",
        "colleague",
        "community_religious",
        "mentor",
        "faded",
        "dormant",
    ]:
        assert expected in CONTEXT_TAGS


# ── Tier enum ────────────────────────────────────────────────────────


def test_tier_from_str_roundtrip():
    for t in Tier:
        assert Tier.from_str(t.value) == t


def test_tier_from_str_case_insensitive():
    assert Tier.from_str("CORE") == Tier.CORE
    assert Tier.from_str("Active") == Tier.ACTIVE


def test_tier_from_str_unknown_fallback():
    assert Tier.from_str("bogus") == Tier.UNKNOWN
    assert Tier.from_str(None) == Tier.UNKNOWN
    assert Tier.from_str("") == Tier.UNKNOWN


def test_pretty_tier_returns_description():
    for t in Tier:
        s = pretty_tier(t)
        assert isinstance(s, str)
        assert len(s) > 0


# ── validate_tags ────────────────────────────────────────────────────


def test_validate_tags_empty():
    assert validate_tags([]) == []
    assert validate_tags(None) == []


def test_validate_tags_keeps_known():
    result = validate_tags(
        [
            {"tag": "family_nuclear", "confidence": 0.9},
            {"tag": "close_friend", "confidence": 0.7},
        ]
    )
    assert len(result) == 2
    tags = {r["tag"] for r in result}
    assert tags == {"family_nuclear", "close_friend"}


def test_validate_tags_drops_unknown():
    result = validate_tags(
        [
            {"tag": "family_nuclear", "confidence": 0.9},
            {"tag": "not_a_real_tag", "confidence": 0.8},
        ]
    )
    assert len(result) == 1
    assert result[0]["tag"] == "family_nuclear"


def test_validate_tags_clamps_confidence():
    result = validate_tags(
        [
            {"tag": "family_nuclear", "confidence": 1.5},
            {"tag": "close_friend", "confidence": -0.2},
        ]
    )
    assert result[0]["confidence"] == 1.0
    assert result[1]["confidence"] == 0.0


def test_validate_tags_dedupe():
    result = validate_tags(
        [
            {"tag": "family_nuclear", "confidence": 0.9},
            {"tag": "family_nuclear", "confidence": 0.8},
        ]
    )
    assert len(result) == 1


def test_validate_tags_accepts_shorthand_strings():
    result = validate_tags(["family_nuclear", "close_friend"])
    assert len(result) == 2
    assert result[0]["confidence"] == 1.0


def test_validate_tags_case_insensitive():
    result = validate_tags(
        [{"tag": "FAMILY_NUCLEAR", "confidence": 0.8}]
    )
    assert len(result) == 1
    assert result[0]["tag"] == "family_nuclear"


def test_validate_tags_alternate_keys():
    # Accept {"name": ..., "score": ...} as alternate shape
    result = validate_tags(
        [{"name": "close_friend", "score": 0.75}]
    )
    assert len(result) == 1
    assert result[0]["tag"] == "close_friend"
    assert result[0]["confidence"] == 0.75


def test_validate_tags_bad_confidence_types():
    result = validate_tags(
        [
            {"tag": "family_nuclear", "confidence": "high"},  # non-numeric
            {"tag": "close_friend", "confidence": None},
        ]
    )
    # Both should fall through to 1.0 default
    assert result[0]["confidence"] == 1.0
    assert result[1]["confidence"] == 1.0


def test_validate_tags_nan_confidence():
    result = validate_tags(
        [{"tag": "family_nuclear", "confidence": float("nan")}]
    )
    assert result[0]["confidence"] == 0.0


# ── ClassificationResult ─────────────────────────────────────────────


def test_classification_result_defaults():
    r = ClassificationResult(person_id="p_test", tier=Tier.ACTIVE)
    assert r.person_id == "p_test"
    assert r.tier == Tier.ACTIVE
    assert r.context_tags == []
    assert r.reasoning == ""
    assert r.model is None
    assert r.run_id.startswith("run_")
    assert r.created_at  # ISO timestamp auto-set


def test_classification_result_roundtrip():
    original = ClassificationResult(
        person_id="p_x",
        tier=Tier.CORE,
        context_tags=[{"tag": "family_nuclear", "confidence": 0.9}],
        reasoning="inner circle family member",
        model="claude-sonnet-4-5",
    )
    d = original.to_dict()
    restored = ClassificationResult.from_dict(d)
    assert restored.person_id == original.person_id
    assert restored.tier == original.tier
    assert restored.context_tags == original.context_tags
    assert restored.reasoning == original.reasoning
    assert restored.model == original.model


def test_classification_from_dict_unknown_tier():
    r = ClassificationResult.from_dict(
        {"person_id": "p", "tier": "bogus_tier"}
    )
    assert r.tier == Tier.UNKNOWN


def test_run_id_is_unique():
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("run_") for i in ids)
