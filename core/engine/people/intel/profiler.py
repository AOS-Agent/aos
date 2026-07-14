"""Profile compiler — aggregates stored signals into a PersonProfile.

The SignalExtractor in Phase 3 produces per-source PersonSignals and writes
them to the ``signal_store`` table. This module reads those rows back,
merges them across sources, and computes a multi-dimensional profile
that downstream classifiers consume.

Inputs  : signal_store rows + (optional) circle_membership rows
Outputs : PersonProfile dataclass with aggregates, recency, density, pattern

No LLM, no side effects beyond reading people.db. Pure aggregation —
same signals → same profile every time.

Feeds:
    classifier.RuleClassifier     — deterministic tier assignment
    classifier.LLMClassifier      — prompt compilation for LLM context tagging
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, SignalStore
from .types import PersonSignals

logger = logging.getLogger(__name__)


# ── Density weights — tunable, documented as module constants ────────
#
# Each weight describes how much a given signal dimension contributes to
# Density is channel-agnostic and recency-weighted. It measures how much
# someone communicates with the operator RIGHT NOW, not historically.
#
# Three components:
#   1. Recent volume (50%) — communication acts in the last 90 days.
#      This is the primary signal. Historical volume doesn't count here.
#   2. Channel diversity (20%) — multi-channel = stronger signal.
#   3. Relationship depth (30%) — all-time volume + calls + presence.
#      Decayed: all-time volume is normalized against a high cap so only
#      truly deep relationships (1000+ msgs) score meaningfully here.
#
# This design is channel-agnostic: adding new channels automatically
# flows into recent_volume and channel_count without weight changes.

DENSITY_RECENT_WEIGHT = 0.50      # recent 90-day volume
DENSITY_CHANNEL_WEIGHT = 0.20     # channel diversity
DENSITY_DEPTH_WEIGHT = 0.30       # all-time depth

DENSITY_RECENT_CAP = 500          # 500+ recent communication acts → max
DENSITY_CHANNEL_CAP = 5           # 5+ channels → max
DENSITY_DEPTH_CAP = 5000          # 5000+ all-time acts → max depth

DENSITY_RANK_HIGH = 0.45
DENSITY_RANK_MEDIUM = 0.20
DENSITY_RANK_LOW = 0.01           # anything above 0 is at least "low"


# ── Dataclass ────────────────────────────────────────────────────────

@dataclass
class PersonProfile:
    """Aggregated, structured profile for a single person.

    All fields are deterministic functions of the stored signals — no
    randomness, no clock reads except for ``days_since_last`` which
    compares to the current UTC time.
    """

    person_id: str
    person_name: str = ""
    source_coverage: list[str] = field(default_factory=list)
    extracted_at: str | None = None

    # Aggregates across all sources
    total_messages: int = 0
    total_calls: int = 0
    total_photos: int = 0
    total_emails: int = 0
    total_mentions: int = 0

    # Channel diversity
    channels_active: list[str] = field(default_factory=list)
    channel_count: int = 0
    is_multi_channel: bool = False      # 3+ channels

    # Temporal
    first_interaction_date: str | None = None
    last_interaction_date: str | None = None
    days_since_last: int | None = None
    span_years: float = 0.0

    # Recency
    recent_volume: int = 0              # communication acts in last 90 days
    recent_outbound: int = 0            # outbound acts in last 90 days

    # Behavioral features
    burstiness: float | None = None     # CV of monthly message counts (high=bursty, low=steady)
    weekend_ratio: float = 0.0          # weekend messages / total
    evening_ratio: float = 0.0          # evening+late_night / total
    response_reciprocity: float = 0.5   # sent / (sent + received), 0.5 = balanced
    media_richness: float = 0.0         # (voice_notes + media + links) / total_messages

    # Density
    density_score: float = 0.0
    density_rank: str = "minimal"       # high | medium | low | minimal

    # Pattern (dominant temporal_pattern across signals)
    dominant_pattern: str = "none"

    # Circles (from circle_membership table; [] if none)
    circles: list[dict] = field(default_factory=list)

    # Metadata richness + flags
    metadata_richness: int = 0
    has_birthday: bool = False
    has_related_names: bool = False
    has_physical_address: bool = False

    # Signal-type presence flags (for classifier heuristics)
    has_communication_signals: bool = False
    has_voice_signals: bool = False
    has_physical_presence_signals: bool = False
    has_professional_signals: bool = False
    has_group_memberships: bool = False
    has_mention_signals: bool = False
    has_metadata_signals: bool = False


# ── Helpers ──────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _normalize(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return _clamp(value / cap)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # ISO 8601 with potential 'Z' suffix.
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _latest_date(*candidates: str | None) -> str | None:
    """Return the most recent ISO date string, or None if all empty."""
    dts: list[tuple[datetime, str]] = []
    for c in candidates:
        dt = _parse_iso(c)
        if dt:
            dts.append((dt, c or ""))
    if not dts:
        return None
    dts.sort(key=lambda x: x[0], reverse=True)
    return dts[0][1]


def _earliest_date(*candidates: str | None) -> str | None:
    dts: list[tuple[datetime, str]] = []
    for c in candidates:
        dt = _parse_iso(c)
        if dt:
            dts.append((dt, c or ""))
    if not dts:
        return None
    dts.sort(key=lambda x: x[0])
    return dts[0][1]


# Precedence order for dominant_pattern — earlier in the tuple wins.
_PATTERN_PRECEDENCE: tuple[str, ...] = (
    "consistent",
    "growing",
    "fading",
    "episodic",
    "clustered",
    "one_shot",
    "none",
)


def _pick_dominant_pattern(patterns: list[str]) -> str:
    seen = {p for p in patterns if p}
    if not seen:
        return "none"
    for candidate in _PATTERN_PRECEDENCE:
        if candidate in seen:
            return candidate
    return "none"


def _collect_signal_dates(signals: PersonSignals) -> tuple[list[str], list[str]]:
    """Return (firsts, lasts) — communication and voice dates only.

    Photos and professional signals contribute to density but NOT to
    recency (days_since_last). A photo from Apple Photos ML face detection
    does not mean you recently communicated with someone. Including photos
    inflated recency for 9 people who were photographed recently but
    hadn't messaged in years.
    """
    firsts: list[str] = []
    lasts: list[str] = []
    for c in signals.communication:
        if c.first_message_date:
            firsts.append(c.first_message_date)
        if c.last_message_date:
            lasts.append(c.last_message_date)
    for v in signals.voice:
        if v.first_call_date:
            firsts.append(v.first_call_date)
        if v.last_call_date:
            lasts.append(v.last_call_date)
    # Professional signals (email) count as communication
    for pro in signals.professional:
        if pro.first_date:
            firsts.append(pro.first_date)
        if pro.last_date:
            lasts.append(pro.last_date)
    # Photos and mentions do NOT drive recency — they're presence signals,
    # not communication signals.
    return firsts, lasts


def _collect_patterns(signals: PersonSignals) -> list[str]:
    out: list[str] = []
    for c in signals.communication:
        out.append(c.temporal_pattern or "none")
    for v in signals.voice:
        out.append(v.temporal_pattern or "none")
    for p in signals.physical_presence:
        out.append(p.temporal_pattern or "none")
    for pro in signals.professional:
        out.append(pro.temporal_pattern or "none")
    return out


def _days_between(iso_a: str | None, iso_b: datetime) -> int | None:
    dt = _parse_iso(iso_a)
    if dt is None:
        return None
    delta = iso_b - dt
    return max(0, delta.days)


# ── Builder ──────────────────────────────────────────────────────────

class ProfileBuilder:
    """Load stored signals and compile them into PersonProfile objects."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.store = SignalStore(self.db_path)

    # ── Public API ──

    def build(self, person_id: str) -> PersonProfile | None:
        """Build a profile for one person. Returns None if no stored signals."""
        signals = self._safe_load(person_id)
        if signals is None:
            return None
        circles = self._load_circles(person_id)
        profile = self._assemble(signals, circles)
        # Enrich with relationship_state data (recent outbound)
        self._enrich_from_relationship_state(profile)
        return profile

    def build_all(
        self, person_ids: list[str] | None = None
    ) -> dict[str, PersonProfile]:
        """Build profiles for a batch of persons.

        If ``person_ids`` is None, builds for every person_id present in
        the signal_store. Persons with no signals are silently skipped.
        """
        if person_ids is None:
            try:
                person_ids = self.store.list_persons()
            except Exception as e:
                logger.debug("list_persons failed: %s", e)
                return {}

        circles_map = self._load_circles_batch(person_ids)

        out: dict[str, PersonProfile] = {}
        for pid in person_ids:
            signals = self._safe_load(pid)
            if signals is None:
                continue
            out[pid] = self._assemble(signals, circles_map.get(pid, []))
        return out

    # ── Internals ──

    def _safe_load(self, person_id: str) -> PersonSignals | None:
        try:
            return self.store.load(person_id)
        except Exception as e:
            logger.debug("profile load failed for %s: %s", person_id, e)
            return None

    def _load_circles(self, person_id: str) -> list[dict]:
        """Query circle + circle_membership for a single person. Best-effort."""
        if not self.db_path.exists():
            return []
        conn = sqlite3.connect(str(self.db_path))
        try:
            return self._fetch_circles(conn, [person_id]).get(person_id, [])
        finally:
            conn.close()

    def _load_circles_batch(self, person_ids: list[str]) -> dict[str, list[dict]]:
        if not person_ids or not self.db_path.exists():
            return {}
        conn = sqlite3.connect(str(self.db_path))
        try:
            out: dict[str, list[dict]] = {}
            CHUNK = 500
            for i in range(0, len(person_ids), CHUNK):
                chunk = person_ids[i : i + CHUNK]
                out.update(self._fetch_circles(conn, chunk))
            return out
        finally:
            conn.close()

    def _fetch_circles(
        self, conn: sqlite3.Connection, person_ids: list[str]
    ) -> dict[str, list[dict]]:
        """Fetch circle memberships for a chunk of persons.

        Both ``circle`` and ``circle_membership`` tables are optional —
        they exist on machines that have run migration 028 but may be
        absent on fresh installs.
        """
        if not person_ids:
            return {}
        placeholders = ",".join("?" * len(person_ids))
        query = (
            f"SELECT cm.person_id, c.name, c.circle_type, cm.confidence "
            f"FROM circle_membership cm "
            f"JOIN circle c ON c.id = cm.circle_id "
            f"WHERE cm.person_id IN ({placeholders})"
        )
        out: dict[str, list[dict]] = {}
        try:
            rows = conn.execute(query, tuple(person_ids)).fetchall()
        except sqlite3.Error as e:
            logger.debug("circle query failed: %s", e)
            return {}
        for person_id, name, ctype, confidence in rows:
            out.setdefault(person_id, []).append(
                {
                    "name": name or "",
                    "type": ctype or "",
                    "confidence": float(confidence) if confidence is not None else 0.0,
                }
            )
        return out

    def _assemble(
        self, signals: PersonSignals, circles: list[dict]
    ) -> PersonProfile:
        profile = PersonProfile(
            person_id=signals.person_id,
            person_name=signals.person_name or "",
            source_coverage=list(signals.source_coverage),
            extracted_at=signals.extracted_at,
        )

        # Aggregates from signals
        profile.total_messages = signals.total_messages
        profile.total_calls = signals.total_calls
        profile.total_photos = signals.total_photos
        profile.total_emails = signals.total_emails
        profile.total_mentions = sum(m.total_mentions for m in signals.mentions)

        # Channels
        profile.channels_active = signals.channels_active
        profile.channel_count = signals.channel_count
        profile.is_multi_channel = signals.is_multi_channel

        # Presence flags
        profile.has_communication_signals = bool(signals.communication)
        profile.has_voice_signals = bool(signals.voice)
        profile.has_physical_presence_signals = bool(signals.physical_presence)
        profile.has_professional_signals = bool(signals.professional)
        profile.has_group_memberships = bool(signals.group_membership)
        profile.has_mention_signals = bool(signals.mentions)
        profile.has_metadata_signals = bool(signals.metadata)

        # Temporal bounds
        firsts, lasts = _collect_signal_dates(signals)
        profile.first_interaction_date = _earliest_date(*firsts)
        profile.last_interaction_date = _latest_date(*lasts)

        now = datetime.now(timezone.utc)
        profile.days_since_last = _days_between(profile.last_interaction_date, now)

        first_dt = _parse_iso(profile.first_interaction_date)
        last_dt = _parse_iso(profile.last_interaction_date)
        if first_dt and last_dt and last_dt >= first_dt:
            profile.span_years = round((last_dt - first_dt).days / 365.25, 2)

        # Dominant pattern
        profile.dominant_pattern = _pick_dominant_pattern(_collect_patterns(signals))

        # Circles
        profile.circles = list(circles)

        # Metadata richness + flags
        total_richness = 0
        has_birthday = False
        has_related = False
        has_addr = False
        for m in signals.metadata:
            total_richness += m.richness_score
            if m.has_birthday:
                has_birthday = True
            if m.has_related_names:
                has_related = True
            if m.has_address:
                has_addr = True
        profile.metadata_richness = total_richness
        profile.has_birthday = has_birthday
        profile.has_related_names = has_related
        profile.has_physical_address = has_addr

        # Recent volume: sum temporal_buckets for last 3 months
        recent_months = set()
        now = datetime.now(timezone.utc)
        for i in range(3):
            dt = now - timedelta(days=30 * i)
            recent_months.add(dt.strftime("%Y-%m"))
        recent_vol = 0
        for sig in signals.communication:
            for month, count in sig.temporal_buckets.items():
                if month in recent_months:
                    recent_vol += count
        for sig in signals.voice:
            for month, count in sig.temporal_buckets.items():
                if month in recent_months:
                    recent_vol += count
        profile.recent_volume = recent_vol

        # Behavioral features
        all_buckets: list[int] = []
        total_sent = 0
        total_received = 0
        total_evening_late = 0.0
        total_weekend_est = 0.0
        total_media = 0
        total_msgs_for_ratios = 0

        for sig in signals.communication:
            all_buckets.extend(sig.temporal_buckets.values())
            total_sent += sig.sent or 0
            total_received += sig.received or 0
            total_msgs_for_ratios += sig.total_messages or 0
            total_evening_late += (sig.evening_pct or 0) * (sig.total_messages or 0)
            total_media += (sig.voice_notes_sent or 0) + (sig.voice_notes_received or 0)
            total_media += (sig.media_sent or 0) + (sig.media_received or 0)
            total_media += (sig.links_shared or 0)

        # Burstiness: coefficient of variation of monthly message counts
        if len(all_buckets) >= 3:
            import statistics
            mean_b = statistics.mean(all_buckets)
            if mean_b > 0:
                std_b = statistics.stdev(all_buckets)
                profile.burstiness = round(std_b / mean_b, 3)

        # Evening ratio (weighted by message count per channel)
        if total_msgs_for_ratios > 0:
            profile.evening_ratio = round(total_evening_late / total_msgs_for_ratios, 3)

        # Reciprocity: sent / (sent + received)
        total_comms = total_sent + total_received
        if total_comms > 0:
            profile.response_reciprocity = round(total_sent / total_comms, 3)

        # Media richness
        if total_msgs_for_ratios > 0:
            profile.media_richness = round(min(total_media / total_msgs_for_ratios, 1.0), 3)

        # Density score + rank
        profile.density_score = self._compute_density(profile)
        profile.density_rank = self._rank_density(profile.density_score)

        return profile

    def _enrich_from_relationship_state(self, profile: PersonProfile) -> None:
        """Pull recent outbound from relationship_state (computed by extraction pipeline)."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT outbound_30d FROM relationship_state WHERE person_id = ?",
                (profile.person_id,),
            ).fetchone()
            if row and row["outbound_30d"]:
                profile.recent_outbound = row["outbound_30d"]
            conn.close()
        except Exception:
            pass  # Graceful — profile still works without this enrichment

    def _compute_density(self, profile: PersonProfile) -> float:
        # Recent volume (50%): communication acts in last 90 days.
        recent = DENSITY_RECENT_WEIGHT * _normalize(
            profile.recent_volume, DENSITY_RECENT_CAP
        )

        # Channel diversity (20%): multi-channel = stronger signal.
        channels = DENSITY_CHANNEL_WEIGHT * _normalize(
            profile.channel_count, DENSITY_CHANNEL_CAP
        )

        # Relationship depth (30%): all-time volume across all types.
        all_time = (
            profile.total_messages
            + profile.total_calls * 10   # calls are higher-signal than texts
            + profile.total_photos
            + profile.total_emails
        )
        depth = DENSITY_DEPTH_WEIGHT * _normalize(all_time, DENSITY_DEPTH_CAP)

        score = recent + channels + depth
        return round(_clamp(score, 0.0, 1.0), 4)

    @staticmethod
    def _rank_density(score: float) -> str:
        if score >= DENSITY_RANK_HIGH:
            return "high"
        if score >= DENSITY_RANK_MEDIUM:
            return "medium"
        if score >= DENSITY_RANK_LOW:
            return "low"
        return "minimal"
