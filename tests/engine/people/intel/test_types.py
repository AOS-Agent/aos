"""Tests for signal type definitions."""
from core.engine.people.intel.types import (
    SignalType,
    SourceCapability,
    PersonSignals,
    CommunicationSignal,
    VoiceSignal,
    PhysicalPresenceSignal,
    ProfessionalSignal,
    GroupSignal,
    MentionSignal,
    MetadataSignal,
)


def test_signal_type_enum():
    assert SignalType.COMMUNICATION.value == "communication"
    assert SignalType.VOICE.value == "voice"
    assert SignalType.PHYSICAL_PRESENCE.value == "physical_presence"
    assert SignalType.PROFESSIONAL.value == "professional"
    assert SignalType.GROUP_MEMBERSHIP.value == "group_membership"
    assert SignalType.MENTION.value == "mention"
    assert SignalType.METADATA.value == "metadata"


def test_source_capability():
    cap = SourceCapability(
        name="apple_messages",
        platform="macos",
        signal_types=[SignalType.COMMUNICATION],
        description="iMessage, SMS, RCS via chat.db",
        requires=["file:~/Library/Messages/chat.db"],
    )
    assert cap.name == "apple_messages"
    assert cap.platform == "macos"
    assert SignalType.COMMUNICATION in cap.signal_types
    assert "file:~/Library/Messages/chat.db" in cap.requires


def test_source_capability_defaults():
    cap = SourceCapability(name="x")
    assert cap.platform == "any"
    assert cap.signal_types == []
    assert cap.description == ""
    assert cap.requires == []


def test_communication_signal_defaults():
    sig = CommunicationSignal(source="test", channel="test")
    assert sig.total_messages == 0
    assert sig.sent == 0
    assert sig.received == 0
    assert sig.first_message_date is None
    assert sig.last_message_date is None
    assert sig.temporal_buckets == {}
    assert sig.temporal_pattern == "none"
    assert sig.response_latency_median is None
    assert sig.time_of_day == {}
    assert sig.late_night_pct == 0
    assert sig.voice_notes_sent == 0
    assert sig.sample_messages == []
    assert sig.service_breakdown == {}


def test_voice_signal_defaults():
    sig = VoiceSignal(source="test")
    assert sig.total_calls == 0
    assert sig.answered_calls == 0
    assert sig.missed_calls == 0
    assert sig.total_minutes == 0
    assert sig.phone_calls == 0
    assert sig.facetime_audio == 0
    assert sig.facetime_video == 0
    assert sig.answer_rate == 0


def test_physical_presence_signal_defaults():
    sig = PhysicalPresenceSignal(source="test")
    assert sig.total_photos == 0
    assert sig.verified is False
    assert sig.locations == []
    assert sig.co_photographed_with == []
    assert sig.operator_taken_pct == 0
    assert sig.detected_age_type is None


def test_professional_signal_defaults():
    sig = ProfessionalSignal(source="test")
    assert sig.total_emails == 0
    assert sig.bidirectional_ratio == 0
    assert sig.subject_keywords == []
    assert sig.subject_categories == {}
    assert sig.thread_count == 0


def test_group_signal_defaults():
    sig = GroupSignal(source="test")
    assert sig.groups == []
    assert sig.total_groups == 0
    assert sig.shared_with_operator == 0
    assert sig.group_categories == {}


def test_mention_signal_defaults():
    sig = MentionSignal(source="test")
    assert sig.total_mentions == 0
    assert sig.mention_contexts == []
    assert sig.daily_log_mentions == 0
    assert sig.session_mentions == 0
    assert sig.work_task_mentions == 0


def test_metadata_signal_defaults():
    sig = MetadataSignal(source="test")
    assert sig.has_birthday is False
    assert sig.birthday is None
    assert sig.addresses == []
    assert sig.related_names == []
    assert sig.social_profiles == []
    assert sig.urls == []
    assert sig.contact_groups == []
    assert sig.richness_score == 0


def test_person_signals_empty():
    ps = PersonSignals(person_id="p_123")
    assert ps.person_id == "p_123"
    assert ps.person_name == ""
    assert ps.source_coverage == []
    assert ps.communication == []
    assert ps.voice == []
    assert ps.total_messages == 0
    assert ps.total_calls == 0
    assert ps.channel_count == 0
    assert ps.is_multi_channel is False


def test_person_signals_merge():
    """PersonSignals from different sources can be merged."""
    s1 = PersonSignals(person_id="p_123", person_name="Alice")
    s1.source_coverage = ["apple_messages"]
    s1.communication.append(CommunicationSignal(
        source="apple_messages",
        channel="imessage",
        total_messages=100,
        sent=40,
        received=60,
    ))

    s2 = PersonSignals(person_id="p_123")
    s2.source_coverage = ["whatsapp"]
    s2.communication.append(CommunicationSignal(
        source="whatsapp",
        channel="whatsapp",
        total_messages=50,
        sent=20,
        received=30,
    ))
    s2.voice.append(VoiceSignal(source="calls", total_calls=5))

    merged = s1.merge(s2)
    assert merged.person_id == "p_123"
    assert merged.person_name == "Alice"  # preserved from s1
    assert len(merged.communication) == 2
    assert len(merged.voice) == 1
    assert "apple_messages" in merged.source_coverage
    assert "whatsapp" in merged.source_coverage
    assert merged.total_messages == 150
    assert merged.total_calls == 5


def test_person_signals_merge_preserves_name_from_other():
    """If self has no name, use other's name."""
    s1 = PersonSignals(person_id="p_1", person_name="")
    s2 = PersonSignals(person_id="p_1", person_name="Bob")
    merged = s1.merge(s2)
    assert merged.person_name == "Bob"


def test_person_signals_channels_active():
    ps = PersonSignals(person_id="p_1")
    ps.communication.append(CommunicationSignal(
        source="imsg", channel="imessage", total_messages=10,
    ))
    ps.communication.append(CommunicationSignal(
        source="wa", channel="whatsapp", total_messages=0,  # zero → not active
    ))
    ps.voice.append(VoiceSignal(source="calls", total_calls=3))
    ps.physical_presence.append(PhysicalPresenceSignal(source="photos", total_photos=2))

    active = ps.channels_active
    assert "imessage" in active
    assert "whatsapp" not in active
    assert "phone" in active
    assert "photos" in active
    assert ps.channel_count == 3
    assert ps.is_multi_channel is True


def test_person_signals_merge_asserts_same_id():
    s1 = PersonSignals(person_id="p_1")
    s2 = PersonSignals(person_id="p_2")
    try:
        s1.merge(s2)
        assert False, "Should have raised"
    except AssertionError:
        pass
