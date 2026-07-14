"""Golden-set regression tests for the People Intelligence classifier.

These tests verify that the classifier produces expected tiers for a set
of synthetic PersonProfile objects with known signal characteristics.
If a code change shifts a tier, the test fails and forces explicit review.

Run with:
    python3 -m pytest tests/engine/people/intel/test_regression.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.engine.people.intel.classifier import RuleClassifier
from core.engine.people.intel.profiler import PersonProfile
from core.engine.people.intel.taxonomy import Tier


@pytest.fixture
def classifier():
    return RuleClassifier()


def _profile(
    person_id: str = "p_test",
    total_messages: int = 0,
    total_calls: int = 0,
    total_photos: int = 0,
    channel_count: int = 0,
    density_rank: str = "minimal",
    days_since_last: int | None = None,
    dominant_pattern: str = "none",
    recent_volume: int = 0,
    recent_outbound: int = 0,
    burstiness: float | None = None,
    evening_ratio: float = 0.0,
    response_reciprocity: float = 0.5,
    **kwargs,
) -> PersonProfile:
    """Build a PersonProfile for testing."""
    p = PersonProfile(person_id=person_id)
    p.total_messages = total_messages
    p.total_calls = total_calls
    p.total_photos = total_photos
    p.channel_count = channel_count
    p.density_rank = density_rank
    p.days_since_last = days_since_last
    p.dominant_pattern = dominant_pattern
    p.recent_volume = recent_volume
    p.recent_outbound = recent_outbound
    p.burstiness = burstiness
    p.evening_ratio = evening_ratio
    p.response_reciprocity = response_reciprocity
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


# ── CORE tier tests ──


def test_core_multi_channel_high_density_recent_reciprocal(classifier):
    """Classic inner circle: 3+ channels, high density, recent, bidirectional, active outbound."""
    p = _profile(
        total_messages=5000, total_calls=50, channel_count=4,
        density_rank="high", days_since_last=2, recent_volume=500,
        response_reciprocity=0.45, recent_outbound=10,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.CORE


def test_core_rejected_without_reciprocity(classifier):
    """High density but one-way → should NOT be core."""
    p = _profile(
        total_messages=5000, channel_count=3,
        density_rank="high", days_since_last=1, recent_volume=500,
        response_reciprocity=0.05, recent_outbound=1,
    )
    result = classifier.classify(p)
    assert result.tier != Tier.CORE


def test_core_rejected_without_recent_outbound(classifier):
    """Good all-time reciprocity but 0 outbound recently → not core."""
    p = _profile(
        total_messages=5000, channel_count=3,
        density_rank="high", days_since_last=1, recent_volume=500,
        response_reciprocity=0.45, recent_outbound=0,
    )
    result = classifier.classify(p)
    assert result.tier != Tier.CORE


# ── ACTIVE tier tests ──


def test_active_multi_channel_medium_density(classifier):
    """Regular contact, 2+ channels, medium density."""
    p = _profile(
        total_messages=200, channel_count=2,
        density_rank="medium", days_since_last=10, recent_volume=50,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.ACTIVE


# ── EMERGING tier tests ──


def test_emerging_growing_pattern(classifier):
    """Growing pattern + recent = emerging."""
    p = _profile(
        total_messages=100, channel_count=1,
        density_rank="low", days_since_last=5,
        dominant_pattern="growing", recent_volume=30,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.EMERGING


def test_emerging_high_burstiness(classifier):
    """Very bursty recent activity triggers emerging."""
    p = _profile(
        total_messages=50, channel_count=1,
        density_rank="low", days_since_last=3,
        burstiness=2.0, recent_volume=25,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.EMERGING


# ── DORMANT tier tests ──


def test_dormant_very_old(classifier):
    """No contact in over a year → dormant."""
    p = _profile(
        total_messages=500, channel_count=2,
        density_rank="medium", days_since_last=400,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.DORMANT


def test_dormant_minimal_density(classifier):
    """Minimal density = dormant regardless of recency."""
    p = _profile(
        total_messages=1, channel_count=1,
        density_rank="minimal", days_since_last=10,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.DORMANT


# ── UNKNOWN tier tests ──


def test_unknown_no_signals(classifier):
    """Zero everything → unknown."""
    p = _profile()
    result = classifier.classify(p)
    assert result.tier == Tier.UNKNOWN


# ── FADING tier tests ──


def test_fading_old_moderate_density(classifier):
    """Old contact with moderate density → fading."""
    p = _profile(
        total_messages=100, channel_count=1,
        density_rank="medium", days_since_last=200,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.FADING


# ── Edge cases ──


def test_channel_specific_single_high_density(classifier):
    """Single channel, high density → channel_specific."""
    p = _profile(
        total_messages=1000, channel_count=1,
        density_rank="high", days_since_last=5, recent_volume=100,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.CHANNEL_SPECIFIC


def test_channel_specific_evening_boost(classifier):
    """Single channel, medium density, but high evening ratio → channel_specific."""
    p = _profile(
        total_messages=200, channel_count=1,
        density_rank="medium", days_since_last=10,
        evening_ratio=0.6, recent_volume=50,
    )
    result = classifier.classify(p)
    assert result.tier == Tier.CHANNEL_SPECIFIC


def test_fresh_install_no_crash(classifier):
    """Empty profile should classify without errors."""
    p = PersonProfile(person_id="p_fresh")
    result = classifier.classify(p)
    assert result.tier in (Tier.UNKNOWN, Tier.DORMANT)
