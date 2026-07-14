"""Signal type definitions for People Intelligence.

Signal types are source-agnostic — they describe WHAT kind of data exists,
not WHERE it came from. Any adapter can produce any signal type.

The PersonSignals container holds all signals for a single person,
organized by type. Multiple sources contribute to the same container
via the merge() method.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SignalType(Enum):
    """Categories of signals about a person."""

    COMMUNICATION = "communication"         # Messages: volume, timing, latency, media
    VOICE = "voice"                         # Calls: frequency, duration, type
    PHYSICAL_PRESENCE = "physical_presence" # Photos: co-occurrence, locations
    PROFESSIONAL = "professional"           # Email, Slack: work context
    GROUP_MEMBERSHIP = "group_membership"   # Shared groups, channels, circles
    MENTION = "mention"                     # Referenced in notes, tasks, sessions
    METADATA = "metadata"                   # Contact info richness, labels, notes


@dataclass
class SourceCapability:
    """Describes what a source adapter provides.

    Used by the adapter registry to report coverage without instantiating
    every adapter.
    """

    name: str
    platform: str = "any"                                      # "macos", "android", "web", "any"
    signal_types: list[SignalType] = field(default_factory=list)
    description: str = ""
    requires: list[str] = field(default_factory=list)          # ["file:path", "oauth:google", ...]


# ── Signal dataclasses ────────────────────────────────────────────────

@dataclass
class CommunicationSignal:
    """Message-based communication signal from any channel."""

    source: str                                                # adapter name
    channel: str                                               # "imessage", "whatsapp", "slack", "sms"
    total_messages: int = 0
    sent: int = 0
    received: int = 0
    first_message_date: str | None = None                      # ISO 8601
    last_message_date: str | None = None
    temporal_buckets: dict[str, int] = field(default_factory=dict)  # YYYY-MM: count
    temporal_pattern: str = "none"                             # consistent, episodic, growing, fading, clustered, one_shot
    avg_message_length: float = 0
    # Response dynamics
    response_latency_median: float | None = None              # minutes
    response_latency_avg: float | None = None
    # Time-of-day distribution
    time_of_day: dict[int, int] = field(default_factory=dict) # hour(0-23): count
    late_night_pct: float = 0                                  # 22:00-05:00
    business_hours_pct: float = 0                              # 09:00-17:00
    evening_pct: float = 0                                     # 17:00-22:00
    # Media
    voice_notes_sent: int = 0
    voice_notes_received: int = 0
    media_sent: int = 0                                        # photos, videos, docs
    media_received: int = 0
    links_shared: int = 0
    reactions_given: int = 0
    reactions_received: int = 0
    # Service type (iMessage-specific)
    service_breakdown: dict[str, int] = field(default_factory=dict)  # {"iMessage": N, "SMS": N}
    # Samples for LLM digest
    sample_messages: list[dict] = field(default_factory=list)  # [{text, date, direction, channel}]


@dataclass
class VoiceSignal:
    """Phone/video call signal."""

    source: str
    total_calls: int = 0
    answered_calls: int = 0
    missed_calls: int = 0
    total_minutes: float = 0
    avg_duration_minutes: float = 0
    max_duration_minutes: float = 0
    outgoing: int = 0
    incoming: int = 0
    first_call_date: str | None = None
    last_call_date: str | None = None
    temporal_buckets: dict[str, int] = field(default_factory=dict)
    temporal_pattern: str = "none"
    time_of_day: dict[int, int] = field(default_factory=dict)
    # Call type breakdown
    phone_calls: int = 0
    facetime_audio: int = 0
    facetime_video: int = 0
    answer_rate: float = 0                                     # answered / total


@dataclass
class PhysicalPresenceSignal:
    """Photo co-occurrence / location signal."""

    source: str
    total_photos: int = 0
    verified: bool = False                                     # user-confirmed face in Photos
    first_photo_date: str | None = None
    last_photo_date: str | None = None
    temporal_buckets: dict[str, int] = field(default_factory=dict)
    temporal_pattern: str = "none"
    # Locations
    locations: list[dict] = field(default_factory=list)        # [{lat, lon, count}]
    home_location_photos: int = 0                              # photos at operator's home
    # Co-occurrence
    co_photographed_with: list[dict] = field(default_factory=list)  # [{name, shared_photos}]
    # Camera source
    operator_taken_pct: float = 0                              # my_camera / total
    received_pct: float = 0                                    # via messaging/airdrop / total
    # Photos ML
    detected_age_type: int | None = None                       # 2=child, 3=young adult, 5=senior
    detected_gender: int | None = None                         # 1=male, 2=female


@dataclass
class ProfessionalSignal:
    """Professional/work context signal (email, Slack, etc.)."""

    source: str
    total_emails: int = 0
    sent_to_you: int = 0
    you_sent: int = 0
    first_date: str | None = None
    last_date: str | None = None
    temporal_buckets: dict[str, int] = field(default_factory=dict)
    temporal_pattern: str = "none"
    bidirectional_ratio: float = 0                             # 0.5 = balanced, 1.0 = all inbound
    # Subject analysis
    subject_keywords: list[str] = field(default_factory=list)  # top keywords from subjects
    subject_categories: dict[str, int] = field(default_factory=dict)
    thread_count: int = 0
    avg_thread_depth: float = 0
    max_thread_depth: int = 0


@dataclass
class GroupSignal:
    """Group/channel co-membership signal."""

    source: str
    groups: list[dict] = field(default_factory=list)           # [{name, type, member_count, role}]
    total_groups: int = 0
    # Co-membership with operator
    shared_with_operator: int = 0
    # Group keyword categories
    group_categories: dict[str, int] = field(default_factory=dict)  # {religious, family, work, social}


@dataclass
class MentionSignal:
    """Person mentioned in notes, tasks, sessions."""

    source: str
    total_mentions: int = 0
    mention_contexts: list[dict] = field(default_factory=list) # [{file, snippet, date}]
    # By sub-source
    daily_log_mentions: int = 0
    session_mentions: int = 0
    work_task_mentions: int = 0


@dataclass
class MetadataSignal:
    """Contact metadata richness signal."""

    source: str
    has_birthday: bool = False
    birthday: str | None = None                                # YYYY-MM-DD
    has_address: bool = False
    addresses: list[dict] = field(default_factory=list)        # [{city, country}]
    has_notes: bool = False
    notes_snippet: str | None = None
    has_social_profiles: bool = False
    social_profiles: list[dict] = field(default_factory=list)  # [{platform, handle}]
    has_related_names: bool = False
    related_names: list[dict] = field(default_factory=list)    # [{label, name}]
    has_urls: bool = False
    urls: list[str] = field(default_factory=list)
    contact_groups: list[str] = field(default_factory=list)    # Apple Contact groups
    organization_raw: str | None = None                        # raw org field
    job_title: str | None = None
    contact_created_at: str | None = None
    richness_score: int = 0                                    # count of non-empty fields


# ── Container ─────────────────────────────────────────────────────────

@dataclass
class PersonSignals:
    """All signals for a single person, from all sources."""

    person_id: str
    person_name: str = ""
    extracted_at: str | None = None                            # ISO timestamp of last extraction
    source_coverage: list[str] = field(default_factory=list)   # which adapters contributed

    communication: list[CommunicationSignal] = field(default_factory=list)
    voice: list[VoiceSignal] = field(default_factory=list)
    physical_presence: list[PhysicalPresenceSignal] = field(default_factory=list)
    professional: list[ProfessionalSignal] = field(default_factory=list)
    group_membership: list[GroupSignal] = field(default_factory=list)
    mentions: list[MentionSignal] = field(default_factory=list)
    metadata: list[MetadataSignal] = field(default_factory=list)

    def merge(self, other: "PersonSignals") -> "PersonSignals":
        """Merge signals from another PersonSignals (same person, different sources).

        Returns a new PersonSignals with combined lists. Does not mutate self.
        """
        assert self.person_id == other.person_id, (
            f"Cannot merge signals for different persons: {self.person_id} vs {other.person_id}"
        )
        merged = PersonSignals(
            person_id=self.person_id,
            person_name=self.person_name or other.person_name,
            extracted_at=self.extracted_at or other.extracted_at,
            source_coverage=sorted(set(self.source_coverage) | set(other.source_coverage)),
        )
        merged.communication = self.communication + other.communication
        merged.voice = self.voice + other.voice
        merged.physical_presence = self.physical_presence + other.physical_presence
        merged.professional = self.professional + other.professional
        merged.group_membership = self.group_membership + other.group_membership
        merged.mentions = self.mentions + other.mentions
        merged.metadata = self.metadata + other.metadata
        return merged

    # ── Computed properties ──

    @property
    def total_messages(self) -> int:
        return sum(c.total_messages for c in self.communication)

    @property
    def total_calls(self) -> int:
        return sum(v.total_calls for v in self.voice)

    @property
    def total_photos(self) -> int:
        return sum(p.total_photos for p in self.physical_presence)

    @property
    def total_emails(self) -> int:
        return sum(p.total_emails for p in self.professional)

    @property
    def channels_active(self) -> list[str]:
        # A channel counts as "active" only with meaningful engagement.
        # A single message (group broadcast, status reply) doesn't count.
        MIN_MESSAGES = 3
        channels = [c.channel for c in self.communication if c.total_messages >= MIN_MESSAGES]
        if self.total_calls > 0:
            channels.append("phone")
        if self.total_photos >= 3:
            channels.append("photos")
        if self.total_emails >= MIN_MESSAGES:
            channels.append("email")
        return channels

    @property
    def channel_count(self) -> int:
        return len(self.channels_active)

    @property
    def is_multi_channel(self) -> bool:
        return self.channel_count >= 3
