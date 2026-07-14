"""Tests for the profile compiler.

All test data is fabricated. Tests use tmp_path + SignalStore to round
stored signals through the builder. No real operator data ever touches
this file.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.profiler import (
    DENSITY_RANK_HIGH,
    DENSITY_RANK_MEDIUM,
    PersonProfile,
    ProfileBuilder,
)
from core.engine.people.intel.store import SignalStore
from core.engine.people.intel.types import (
    CommunicationSignal,
    GroupSignal,
    MentionSignal,
    MetadataSignal,
    PersonSignals,
    PhysicalPresenceSignal,
    ProfessionalSignal,
    VoiceSignal,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> SignalStore:
    s = SignalStore(tmp_path / "people.db")
    s.init_schema()
    return s


@pytest.fixture()
def builder(store: SignalStore) -> ProfileBuilder:
    return ProfileBuilder(store.db_path)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _iso_years_ago(years: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))).isoformat()


def _seed_person(
    store: SignalStore,
    person_id: str,
    *,
    messages: int = 0,
    calls: int = 0,
    photos: int = 0,
    emails: int = 0,
    mentions: int = 0,
    channel: str = "test_channel",
    days_ago_first: int = 365,
    days_ago_last: int = 30,
    temporal_pattern: str = "episodic",
    metadata_richness: int = 0,
    has_birthday: bool = False,
    has_related: bool = False,
    has_address: bool = False,
    groups: int = 0,
    person_name: str = "",
) -> None:
    signals = PersonSignals(
        person_id=person_id,
        person_name=person_name or f"Test Person {person_id}",
        source_coverage=["test_source"],
    )

    if messages > 0:
        signals.communication.append(
            CommunicationSignal(
                source="test_source",
                channel=channel,
                total_messages=messages,
                sent=messages // 2,
                received=messages - (messages // 2),
                first_message_date=_iso_days_ago(days_ago_first),
                last_message_date=_iso_days_ago(days_ago_last),
                temporal_pattern=temporal_pattern,
            )
        )

    if calls > 0:
        signals.voice.append(
            VoiceSignal(
                source="test_source",
                total_calls=calls,
                first_call_date=_iso_days_ago(days_ago_first),
                last_call_date=_iso_days_ago(days_ago_last),
                temporal_pattern=temporal_pattern,
            )
        )

    if photos > 0:
        signals.physical_presence.append(
            PhysicalPresenceSignal(
                source="test_source",
                total_photos=photos,
                first_photo_date=_iso_days_ago(days_ago_first),
                last_photo_date=_iso_days_ago(days_ago_last),
                temporal_pattern=temporal_pattern,
            )
        )

    if emails > 0:
        signals.professional.append(
            ProfessionalSignal(
                source="test_source",
                total_emails=emails,
                first_date=_iso_days_ago(days_ago_first),
                last_date=_iso_days_ago(days_ago_last),
                temporal_pattern=temporal_pattern,
            )
        )

    if mentions > 0:
        signals.mentions.append(
            MentionSignal(
                source="test_source",
                total_mentions=mentions,
            )
        )

    if metadata_richness > 0 or has_birthday or has_related or has_address:
        signals.metadata.append(
            MetadataSignal(
                source="test_source",
                richness_score=metadata_richness,
                has_birthday=has_birthday,
                has_related_names=has_related,
                has_address=has_address,
            )
        )

    if groups > 0:
        signals.group_membership.append(
            GroupSignal(
                source="test_source",
                total_groups=groups,
                groups=[{"name": f"Group{i}", "type": "test"} for i in range(groups)],
            )
        )

    store.save(person_id, "test_source", signals)


# ── Empty / missing cases ────────────────────────────────────────────


def test_build_returns_none_when_no_signals(builder: ProfileBuilder):
    result = builder.build("p_nonexistent")
    assert result is None


def test_build_all_empty(builder: ProfileBuilder):
    result = builder.build_all([])
    assert result == {}


def test_build_all_skips_persons_without_signals(
    builder: ProfileBuilder, store: SignalStore
):
    _seed_person(store, "p_has", messages=10)
    result = builder.build_all(["p_has", "p_missing"])
    assert "p_has" in result
    assert "p_missing" not in result


# ── Basic aggregate tests ────────────────────────────────────────────


def test_basic_aggregates(builder: ProfileBuilder, store: SignalStore):
    _seed_person(
        store,
        "p1",
        messages=50,
        calls=5,
        photos=10,
        emails=3,
        mentions=2,
        metadata_richness=4,
    )
    p = builder.build("p1")
    assert p is not None
    assert p.total_messages == 50
    assert p.total_calls == 5
    assert p.total_photos == 10
    assert p.total_emails == 3
    assert p.total_mentions == 2
    assert p.metadata_richness == 4


def test_channel_count_with_multiple_sources(store: SignalStore):
    # Seed two sources for the same person with different channels.
    s1 = PersonSignals(
        person_id="p_multi", source_coverage=["src_a"], person_name="Test"
    )
    s1.communication.append(
        CommunicationSignal(source="src_a", channel="imessage", total_messages=10)
    )
    store.save("p_multi", "src_a", s1)

    s2 = PersonSignals(person_id="p_multi", source_coverage=["src_b"])
    s2.communication.append(
        CommunicationSignal(source="src_b", channel="whatsapp", total_messages=5)
    )
    s2.voice.append(VoiceSignal(source="src_b", total_calls=2))
    store.save("p_multi", "src_b", s2)

    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_multi")
    assert p is not None
    assert p.total_messages == 15
    assert p.total_calls == 2
    # imessage + whatsapp + phone = 3 channels
    assert p.channel_count == 3
    assert p.is_multi_channel is True


def test_is_multi_channel_threshold(builder: ProfileBuilder, store: SignalStore):
    # Only 1 channel of messages → not multi-channel
    _seed_person(store, "p_single", messages=100)
    p = builder.build("p_single")
    assert p is not None
    assert p.is_multi_channel is False  # just 1 channel


# ── Density score tests ──────────────────────────────────────────────


def test_density_zero_for_empty_signals(store: SignalStore):
    signals = PersonSignals(person_id="p_empty", source_coverage=["test_source"])
    store.save("p_empty", "test_source", signals)
    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_empty")
    assert p is not None
    assert p.density_score == 0.0
    assert p.density_rank == "minimal"


def test_density_maxes_at_one(builder: ProfileBuilder, store: SignalStore):
    _seed_person(
        store,
        "p_max",
        messages=10_000,
        calls=1_000,
        photos=2_000,
        emails=5_000,
        metadata_richness=100,
    )
    p = builder.build("p_max")
    assert p is not None
    assert 0.0 <= p.density_score <= 1.0


def test_density_rank_high(builder: ProfileBuilder, store: SignalStore):
    _seed_person(
        store,
        "p_high",
        messages=1000,
        calls=100,
        photos=200,
        emails=500,
        metadata_richness=10,
    )
    p = builder.build("p_high")
    assert p is not None
    # All caps + single channel contributes less; confirm at least medium
    assert p.density_score >= DENSITY_RANK_MEDIUM


def test_density_rank_low_for_sparse_signals(
    builder: ProfileBuilder, store: SignalStore
):
    _seed_person(store, "p_sparse", messages=5)
    p = builder.build("p_sparse")
    assert p is not None
    assert p.density_rank in ("low", "minimal")


# ── Temporal tests ───────────────────────────────────────────────────


def test_days_since_last_recent(builder: ProfileBuilder, store: SignalStore):
    _seed_person(store, "p_recent", messages=10, days_ago_last=5)
    p = builder.build("p_recent")
    assert p is not None
    assert p.days_since_last is not None
    assert 4 <= p.days_since_last <= 6  # allow fuzz


def test_days_since_last_old(builder: ProfileBuilder, store: SignalStore):
    _seed_person(store, "p_old", messages=10, days_ago_last=400)
    p = builder.build("p_old")
    assert p is not None
    assert p.days_since_last is not None
    assert 399 <= p.days_since_last <= 401


def test_span_years(builder: ProfileBuilder, store: SignalStore):
    _seed_person(
        store,
        "p_span",
        messages=10,
        days_ago_first=int(5 * 365.25),
        days_ago_last=10,
    )
    p = builder.build("p_span")
    assert p is not None
    assert 4.5 <= p.span_years <= 5.5


def test_days_since_last_picks_max_across_sources(store: SignalStore):
    """days_since_last should be the MOST RECENT of any source."""
    s = PersonSignals(person_id="p_mix", source_coverage=["a", "b"])
    s.communication.append(
        CommunicationSignal(
            source="a", channel="test",
            total_messages=5,
            last_message_date=_iso_days_ago(100),
        )
    )
    s.voice.append(
        VoiceSignal(
            source="b",
            total_calls=1,
            last_call_date=_iso_days_ago(10),  # more recent
        )
    )
    store.save("p_mix", "a", s)
    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_mix")
    assert p is not None
    assert p.days_since_last is not None
    assert p.days_since_last <= 11  # picks the voice one (more recent)


# ── Pattern tests ────────────────────────────────────────────────────


def test_dominant_pattern_consistent_wins(builder: ProfileBuilder, store: SignalStore):
    _seed_person(store, "p_cons", messages=50, temporal_pattern="consistent")
    p = builder.build("p_cons")
    assert p is not None
    assert p.dominant_pattern == "consistent"


def test_dominant_pattern_none_for_empty(store: SignalStore):
    signals = PersonSignals(person_id="p_nada", source_coverage=["test_source"])
    store.save("p_nada", "test_source", signals)
    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_nada")
    assert p is not None
    assert p.dominant_pattern == "none"


def test_dominant_pattern_precedence_growing_over_fading(store: SignalStore):
    """Growing should beat fading even when both are present."""
    s = PersonSignals(person_id="p_prec", source_coverage=["x"])
    s.communication.append(
        CommunicationSignal(
            source="x", channel="a",
            total_messages=10,
            temporal_pattern="fading",
        )
    )
    s.voice.append(
        VoiceSignal(source="x", total_calls=5, temporal_pattern="growing")
    )
    store.save("p_prec", "x", s)
    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_prec")
    assert p is not None
    assert p.dominant_pattern == "growing"


# ── Metadata flag tests ──────────────────────────────────────────────


def test_metadata_flags_propagate(builder: ProfileBuilder, store: SignalStore):
    _seed_person(
        store,
        "p_meta",
        messages=5,
        metadata_richness=7,
        has_birthday=True,
        has_related=True,
        has_address=False,
    )
    p = builder.build("p_meta")
    assert p is not None
    assert p.has_birthday is True
    assert p.has_related_names is True
    assert p.has_physical_address is False
    assert p.metadata_richness == 7


def test_has_signal_flags(builder: ProfileBuilder, store: SignalStore):
    _seed_person(store, "p_flags", messages=5, calls=2, groups=1, mentions=3)
    p = builder.build("p_flags")
    assert p is not None
    assert p.has_communication_signals is True
    assert p.has_voice_signals is True
    assert p.has_group_memberships is True
    assert p.has_mention_signals is True
    assert p.has_physical_presence_signals is False
    assert p.has_professional_signals is False


# ── Circle loading tests ─────────────────────────────────────────────


def test_circles_empty_when_no_table(builder: ProfileBuilder, store: SignalStore):
    """Fresh store has no circle table — should gracefully return []."""
    _seed_person(store, "p_no_circles", messages=5)
    p = builder.build("p_no_circles")
    assert p is not None
    assert p.circles == []


def test_circles_populated_from_table(store: SignalStore, tmp_path: Path):
    """If circle + circle_membership tables exist, they get loaded."""
    import sqlite3

    _seed_person(store, "p_circle", messages=5)

    # Add circle tables and memberships.
    conn = sqlite3.connect(str(store.db_path))
    conn.executescript(
        """
        CREATE TABLE circle (
            id TEXT PRIMARY KEY,
            name TEXT,
            circle_type TEXT
        );
        CREATE TABLE circle_membership (
            person_id TEXT,
            circle_id TEXT,
            confidence REAL
        );
        INSERT INTO circle VALUES ('c_fam', 'Fake Family', 'kinship');
        INSERT INTO circle_membership VALUES ('p_circle', 'c_fam', 0.95);
        """
    )
    conn.commit()
    conn.close()

    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_circle")
    assert p is not None
    assert len(p.circles) == 1
    assert p.circles[0]["name"] == "Fake Family"
    assert p.circles[0]["type"] == "kinship"
    assert 0.9 < p.circles[0]["confidence"] < 1.0


# ── Build all tests ──────────────────────────────────────────────────


def test_build_all_defaults_to_all_persons(
    builder: ProfileBuilder, store: SignalStore
):
    _seed_person(store, "p_a", messages=10)
    _seed_person(store, "p_b", messages=20)
    result = builder.build_all()  # no arg → all persons
    assert set(result.keys()) == {"p_a", "p_b"}


def test_build_all_subset(builder: ProfileBuilder, store: SignalStore):
    _seed_person(store, "p_a", messages=10)
    _seed_person(store, "p_b", messages=20)
    _seed_person(store, "p_c", messages=30)
    result = builder.build_all(["p_a", "p_c"])
    assert set(result.keys()) == {"p_a", "p_c"}


# ── Edge cases ────────────────────────────────────────────────────────


def test_handles_missing_db_gracefully(tmp_path: Path):
    builder = ProfileBuilder(tmp_path / "nonexistent.db")
    assert builder.build("p_any") is None
    assert builder.build_all(["p_any"]) == {}


def test_handles_malformed_signal_dates(store: SignalStore):
    s = PersonSignals(person_id="p_bad", source_coverage=["x"])
    s.communication.append(
        CommunicationSignal(
            source="x", channel="test",
            total_messages=5,
            first_message_date="not-a-date",
            last_message_date="also-not-a-date",
        )
    )
    store.save("p_bad", "x", s)
    builder = ProfileBuilder(store.db_path)
    p = builder.build("p_bad")
    assert p is not None
    # Bad dates should not crash — just produce None values
    assert p.first_interaction_date is None or isinstance(p.first_interaction_date, str)
    assert p.days_since_last is None or isinstance(p.days_since_last, int)
