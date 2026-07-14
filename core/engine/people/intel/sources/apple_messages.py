"""Apple Messages signal adapter.

Extracts iMessage/SMS/RCS communication signals from the local
chat.db SQLite database at ~/Library/Messages/chat.db.

The adapter:
  * Copies chat.db to a temp directory before reading (avoids lock risk).
  * Matches handles (phone numbers, emails) to person_ids using
    last-10-digit suffix matching for phones and exact lowercase
    matching for emails.
  * Aggregates per-person communication signals (volume, timing,
    latency, media, links, reactions, service breakdown, samples).

Chat.db timestamps are nanoseconds since the Apple epoch (2001-01-01).
Convert with: unix_ts = (ns / 1e9) + 978307200.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ..types import CommunicationSignal, PersonSignals, SignalType
from .base import SignalAdapter

log = logging.getLogger(__name__)

# Apple epoch offset: seconds from 1970-01-01 to 2001-01-01.
APPLE_EPOCH_OFFSET = 978307200

# iMessage "tapback" reaction types live in associated_message_type.
REACTION_TYPES = {2000, 2001, 2002, 2003, 2004, 2005}

DEFAULT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"


def _apple_ns_to_unix(ns: int | float) -> float:
    """Convert Apple chat.db nanosecond timestamp to unix seconds."""
    return (ns / 1e9) + APPLE_EPOCH_OFFSET


def _apple_ns_to_iso(ns: int | float) -> str:
    """Convert Apple chat.db nanosecond timestamp to ISO 8601 UTC string."""
    return datetime.fromtimestamp(_apple_ns_to_unix(ns), tz=timezone.utc).isoformat()


def _phone_suffix(phone: str) -> str | None:
    """Return the last 10 digits of a phone number, or None if <10 digits."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    return digits[-10:]


def _compute_temporal_pattern(buckets: dict[str, int]) -> str:
    """Classify temporal_pattern from YYYY-MM buckets."""
    if not buckets:
        return "none"
    if len(buckets) == 1:
        return "one_shot"

    sorted_keys = sorted(buckets.keys())

    # consecutive run of months with >=5 messages
    def _month_plus(k: str, n: int) -> str:
        y, m = k.split("-")
        y_i, m_i = int(y), int(m) + n
        while m_i > 12:
            y_i += 1
            m_i -= 12
        return f"{y_i:04d}-{m_i:02d}"

    consecutive_run = 0
    for i, k in enumerate(sorted_keys):
        if buckets[k] >= 5:
            if i == 0 or sorted_keys[i - 1] == _month_plus(k, -1):
                consecutive_run += 1
                if consecutive_run >= 3:
                    break
            else:
                consecutive_run = 1
        else:
            consecutive_run = 0

    consistent = consecutive_run >= 3

    # growing / fading via first-3 vs last-3 avg
    first3 = sorted_keys[:3]
    last3 = sorted_keys[-3:]
    first_avg = statistics.mean(buckets[k] for k in first3) if first3 else 0
    last_avg = statistics.mean(buckets[k] for k in last3) if last3 else 0

    if consistent:
        return "consistent"
    if first_avg > 0 and last_avg >= first_avg * 2:
        return "growing"
    if last_avg > 0 and first_avg >= last_avg * 2:
        return "fading"

    # episodic = sparse months with gaps
    if len(sorted_keys) >= 2:
        gaps = 0
        for a, b in zip(sorted_keys, sorted_keys[1:]):
            if b != _month_plus(a, 1):
                gaps += 1
        if gaps > 0:
            return "episodic"

    return "clustered"


class AppleMessagesAdapter(SignalAdapter):
    """Signal adapter for Apple Messages (iMessage/SMS/RCS)."""

    name: ClassVar[str] = "apple_messages"
    display_name: ClassVar[str] = "Apple Messages"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [SignalType.COMMUNICATION]
    description: ClassVar[str] = "iMessage + SMS + RCS via ~/Library/Messages/chat.db"
    requires: ClassVar[list[str]] = ["file:~/Library/Messages/chat.db"]

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # ── availability ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            return self.db_path.exists() and os.access(self.db_path, os.R_OK)
        except Exception:
            return False

    # ── extraction ────────────────────────────────────────────────────

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            return {}

        try:
            return self._extract_all_inner(person_index)
        except Exception as e:  # pragma: no cover - defensive
            log.exception("apple_messages extract_all failed: %s", e)
            return {}

    def _extract_all_inner(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        # Build lookup maps from person_index.
        phone_suffix_to_pid: dict[str, str] = {}
        ambiguous_suffixes: set[str] = set()
        email_to_pid: dict[str, str] = {}

        for pid, info in person_index.items():
            for phone in info.get("phones") or []:
                suffix = _phone_suffix(phone)
                if not suffix:
                    continue
                existing = phone_suffix_to_pid.get(suffix)
                if existing and existing != pid:
                    ambiguous_suffixes.add(suffix)
                else:
                    phone_suffix_to_pid[suffix] = pid
            for email in info.get("emails") or []:
                if not email:
                    continue
                key = email.strip().lower()
                if not key:
                    continue
                existing = email_to_pid.get(key)
                if existing and existing != pid:
                    # Email collision — drop.
                    email_to_pid.pop(key, None)
                else:
                    email_to_pid[key] = pid

        for s in ambiguous_suffixes:
            phone_suffix_to_pid.pop(s, None)

        # Copy chat.db to a temp directory to avoid lock conflicts.
        with tempfile.TemporaryDirectory(prefix="aos-intel-imsg-") as tmpdir:
            tmp_db = Path(tmpdir) / "chat.db"
            try:
                shutil.copy2(self.db_path, tmp_db)
            except Exception as e:
                log.warning("apple_messages: could not copy chat.db: %s", e)
                return {}

            conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                return self._scan_and_group(
                    conn, person_index, phone_suffix_to_pid, email_to_pid
                )
            finally:
                conn.close()

    def _match_handle_to_pid(
        self,
        handle_id: str,
        phone_suffix_to_pid: dict[str, str],
        email_to_pid: dict[str, str],
    ) -> str | None:
        if not handle_id:
            return None
        if "@" in handle_id:
            return email_to_pid.get(handle_id.strip().lower())
        suffix = _phone_suffix(handle_id)
        if not suffix:
            return None
        return phone_suffix_to_pid.get(suffix)

    def _scan_and_group(
        self,
        conn: sqlite3.Connection,
        person_index: dict[str, dict],
        phone_suffix_to_pid: dict[str, str],
        email_to_pid: dict[str, str],
    ) -> dict[str, PersonSignals]:
        # Step 1: map handle ROWID → person_id.
        handle_to_pid: dict[int, str] = {}
        try:
            rows = conn.execute("SELECT ROWID, id FROM handle").fetchall()
        except sqlite3.Error as e:
            log.warning("apple_messages: handle query failed: %s", e)
            return {}

        for row in rows:
            pid = self._match_handle_to_pid(
                row["id"] or "", phone_suffix_to_pid, email_to_pid
            )
            if pid is not None:
                handle_to_pid[int(row["ROWID"])] = pid

        if not handle_to_pid:
            return {}

        # Step 2: group handles by person_id.
        pid_to_handles: dict[str, list[int]] = {}
        for hid, pid in handle_to_pid.items():
            pid_to_handles.setdefault(pid, []).append(hid)

        # Step 3: for each person, pull messages via the join path
        # handle → chat_handle_join → chat_message_join → message.
        results: dict[str, PersonSignals] = {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for pid, handle_ids in pid_to_handles.items():
            try:
                rows = self._fetch_messages_for_handles(conn, handle_ids)
            except sqlite3.Error as e:
                log.warning(
                    "apple_messages: message fetch failed for %s: %s", pid, e
                )
                continue

            if not rows:
                continue

            signal = self._build_comm_signal(rows)
            if signal.total_messages == 0:
                continue

            info = person_index.get(pid) or {}
            ps = PersonSignals(
                person_id=pid,
                person_name=info.get("name", ""),
                extracted_at=now_iso,
                source_coverage=[self.name],
            )
            ps.communication.append(signal)
            results[pid] = ps

        return results

    def _fetch_messages_for_handles(
        self, conn: sqlite3.Connection, handle_ids: list[int]
    ) -> list[sqlite3.Row]:
        """Fetch messages for a person's handles, separating 1:1 from group.

        Only counts messages from 1:1 chats (style=45). Group chat messages
        (style=43) are excluded because they inflate per-person counts —
        a message from Person B in a group also containing Person A would be
        counted for Person A, creating false signal.
        """
        placeholders = ",".join("?" for _ in handle_ids)
        query = f"""
            SELECT DISTINCT
                m.ROWID AS rowid,
                m.text AS text,
                m.date AS date,
                m.is_from_me AS is_from_me,
                m.service AS service,
                m.associated_message_type AS associated_message_type,
                m.cache_has_attachments AS attached_file_count
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON cmj.chat_id = c.ROWID
            JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            WHERE chj.handle_id IN ({placeholders})
              AND c.style = 45
            ORDER BY m.date ASC
        """
        return conn.execute(query, handle_ids).fetchall()

    # ── signal construction ──────────────────────────────────────────

    def _build_comm_signal(self, rows: list[sqlite3.Row]) -> CommunicationSignal:
        sig = CommunicationSignal(source=self.name, channel="imessage")

        # De-duplicate on rowid (DISTINCT should cover it, but belt+suspenders).
        seen: set[int] = set()
        unique_rows: list[sqlite3.Row] = []
        for r in rows:
            rid = int(r["rowid"])
            if rid in seen:
                continue
            seen.add(rid)
            unique_rows.append(r)

        if not unique_rows:
            return sig

        sig.total_messages = len(unique_rows)

        lengths: list[int] = []
        hour_counts: dict[int, int] = {}
        buckets: dict[str, int] = {}
        service_counts: dict[str, int] = {}
        first_date_ns: int | None = None
        last_date_ns: int | None = None
        late_night = 0
        business = 0
        evening = 0
        links = 0

        # Collect samples from the newest end.
        sample_candidates: list[tuple[int, str, int, str]] = []

        for r in unique_rows:
            ns = int(r["date"] or 0)
            if first_date_ns is None or ns < first_date_ns:
                first_date_ns = ns
            if last_date_ns is None or ns > last_date_ns:
                last_date_ns = ns

            is_from_me = int(r["is_from_me"] or 0)
            if is_from_me == 1:
                sig.sent += 1
            else:
                sig.received += 1

            text = r["text"]
            if text:
                lengths.append(len(text))
                if "http://" in text or "https://" in text:
                    links += 1

            # Time-of-day.
            try:
                dt = datetime.fromtimestamp(_apple_ns_to_unix(ns), tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                dt = None
            if dt is not None:
                hr = dt.hour
                hour_counts[hr] = hour_counts.get(hr, 0) + 1
                if hr >= 22 or hr < 5:
                    late_night += 1
                if 9 <= hr < 17:
                    business += 1
                if 17 <= hr < 22:
                    evening += 1
                bucket_key = f"{dt.year:04d}-{dt.month:02d}"
                buckets[bucket_key] = buckets.get(bucket_key, 0) + 1

            # Service breakdown.
            svc = r["service"] or "Unknown"
            service_counts[svc] = service_counts.get(svc, 0) + 1

            # Reactions via associated_message_type.
            amt = int(r["associated_message_type"] or 0)
            if amt in REACTION_TYPES:
                if is_from_me == 1:
                    sig.reactions_given += 1
                else:
                    sig.reactions_received += 1

            # Attachments.
            afc = int(r["attached_file_count"] or 0)
            if afc > 0:
                if is_from_me == 1:
                    sig.media_sent += 1
                else:
                    sig.media_received += 1

            # Sample candidate if text is non-empty.
            if text:
                direction = "sent" if is_from_me == 1 else "received"
                sample_candidates.append((ns, text, is_from_me, direction))

        if first_date_ns is not None:
            sig.first_message_date = _apple_ns_to_iso(first_date_ns)
        if last_date_ns is not None:
            sig.last_message_date = _apple_ns_to_iso(last_date_ns)

        if lengths:
            sig.avg_message_length = float(statistics.mean(lengths))

        sig.time_of_day = hour_counts
        sig.temporal_buckets = buckets
        sig.temporal_pattern = _compute_temporal_pattern(buckets)
        sig.service_breakdown = service_counts
        sig.links_shared = links

        total = sig.total_messages
        if total > 0:
            sig.late_night_pct = late_night / total
            sig.business_hours_pct = business / total
            sig.evening_pct = evening / total

        # Response latency via direction flips.
        flips_minutes: list[float] = []
        prev_ns: int | None = None
        prev_dir: int | None = None
        for r in unique_rows:
            ns = int(r["date"] or 0)
            is_from_me = int(r["is_from_me"] or 0)
            if prev_ns is not None and prev_dir is not None and prev_dir != is_from_me:
                gap_sec = (ns - prev_ns) / 1e9
                if gap_sec >= 0:
                    flips_minutes.append(gap_sec / 60.0)
            prev_ns = ns
            prev_dir = is_from_me

        if flips_minutes:
            sig.response_latency_median = float(statistics.median(flips_minutes))
            sig.response_latency_avg = float(statistics.mean(flips_minutes))

        # Sample messages: up to 5 most recent non-empty.
        sample_candidates.sort(key=lambda t: t[0], reverse=True)
        samples: list[dict] = []
        for ns, text, _is_from_me, direction in sample_candidates[:5]:
            samples.append(
                {
                    "text": text,
                    "date": _apple_ns_to_iso(ns),
                    "direction": direction,
                    "channel": "imessage",
                }
            )
        sig.sample_messages = samples

        return sig
