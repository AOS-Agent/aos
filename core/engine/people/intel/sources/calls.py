"""Call history signal adapter.

Extracts voice signals (phone calls + FaceTime audio/video) from the
local CallHistoryDB SQLite database at:
  ~/Library/Application Support/CallHistoryDB/CallHistory.storedata

This single database contains phone calls AND FaceTime (both audio and
video). The separate ~/Library/Application Support/FaceTime/FaceTime.sqlite3
file is empty on modern macOS and should NOT be used.

The adapter:
  * Copies the .storedata file to a temp directory before reading
    (avoids lock risk).
  * Matches ZADDRESS values to person_ids using last-10-digit suffix
    matching for phones and exact lowercase matching for emails
    (FaceTime identifies with email handles).
  * Aggregates per-person voice signals (counts, durations, type
    breakdown, temporal pattern).

CallHistoryDB uses Core Data timestamps: seconds since 2001-01-01.
Convert with: unix_ts = z_date + 978307200.

ZCALLTYPE canonical values (verified in prior extraction research):
   1 = phone call
   8 = FaceTime audio
  16 = FaceTime video
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

from ..types import PersonSignals, SignalType, VoiceSignal
from .base import SignalAdapter

log = logging.getLogger(__name__)

# Core Data epoch offset: seconds from 1970-01-01 to 2001-01-01.
CORE_DATA_EPOCH_OFFSET = 978307200

DEFAULT_DB_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "CallHistoryDB"
    / "CallHistory.storedata"
)

# ZCALLTYPE values.
CALL_TYPE_PHONE = 1
CALL_TYPE_FACETIME_AUDIO = 8
CALL_TYPE_FACETIME_VIDEO = 16


def _core_data_to_unix(z_date: float) -> float:
    """Convert Core Data timestamp (sec since 2001-01-01) to unix seconds."""
    return float(z_date) + CORE_DATA_EPOCH_OFFSET


def _core_data_to_iso(z_date: float) -> str:
    """Convert Core Data timestamp to ISO 8601 UTC string."""
    return datetime.fromtimestamp(_core_data_to_unix(z_date), tz=timezone.utc).isoformat()


def _phone_suffix(phone: str) -> str | None:
    """Return the last 10 digits of a phone number, or None if <10 digits."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    return digits[-10:]


def _decode_address(value) -> str | None:
    """Decode a ZADDRESS value — may be bytes (BLOB) or str."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8").strip()
        except (UnicodeDecodeError, AttributeError):
            try:
                return value.decode("utf-8", errors="ignore").strip()
            except Exception:
                return None
    if isinstance(value, str):
        return value.strip()
    try:
        return str(value).strip()
    except Exception:
        return None


def _compute_temporal_pattern(buckets: dict[str, int]) -> str:
    """Classify temporal_pattern from YYYY-MM buckets.

    Matches the heuristic used by apple_messages so signal consumers
    see consistent vocabulary across channels.
    """
    if not buckets:
        return "none"
    if len(buckets) == 1:
        return "one_shot"

    sorted_keys = sorted(buckets.keys())

    def _month_plus(k: str, n: int) -> str:
        y, m = k.split("-")
        y_i, m_i = int(y), int(m) + n
        while m_i > 12:
            y_i += 1
            m_i -= 12
        while m_i < 1:
            y_i -= 1
            m_i += 12
        return f"{y_i:04d}-{m_i:02d}"

    # Note: calls are rarer than messages — use threshold of 3, not 5.
    consecutive_run = 0
    for i, k in enumerate(sorted_keys):
        if buckets[k] >= 3:
            if i == 0 or sorted_keys[i - 1] == _month_plus(k, -1):
                consecutive_run += 1
                if consecutive_run >= 3:
                    break
            else:
                consecutive_run = 1
        else:
            consecutive_run = 0

    consistent = consecutive_run >= 3

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

    if len(sorted_keys) >= 2:
        gaps = 0
        for a, b in zip(sorted_keys, sorted_keys[1:]):
            if b != _month_plus(a, 1):
                gaps += 1
        if gaps > 0:
            return "episodic"

    return "clustered"


class CallHistoryAdapter(SignalAdapter):
    """Signal adapter for macOS Call History (phone + FaceTime)."""

    name: ClassVar[str] = "calls"
    display_name: ClassVar[str] = "Call History"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [SignalType.VOICE]
    description: ClassVar[str] = (
        "Phone calls + FaceTime audio/video via CallHistoryDB"
    )
    requires: ClassVar[list[str]] = [
        "file:~/Library/Application Support/CallHistoryDB/CallHistory.storedata"
    ]

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
            log.exception("calls extract_all failed: %s", e)
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
                    email_to_pid.pop(key, None)
                else:
                    email_to_pid[key] = pid

        for s in ambiguous_suffixes:
            phone_suffix_to_pid.pop(s, None)

        # Copy the .storedata file to temp to avoid lock conflicts.
        with tempfile.TemporaryDirectory(prefix="aos-intel-calls-") as tmpdir:
            tmp_db = Path(tmpdir) / "CallHistory.storedata"
            try:
                shutil.copy2(self.db_path, tmp_db)
            except Exception as e:
                log.warning("calls: could not copy CallHistory.storedata: %s", e)
                return {}

            try:
                conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            except sqlite3.Error as e:
                log.warning("calls: could not open CallHistory.storedata: %s", e)
                return {}

            try:
                conn.row_factory = sqlite3.Row
                return self._scan_and_group(
                    conn, person_index, phone_suffix_to_pid, email_to_pid
                )
            finally:
                conn.close()

    def _match_address_to_pid(
        self,
        address: str,
        phone_suffix_to_pid: dict[str, str],
        email_to_pid: dict[str, str],
    ) -> str | None:
        if not address:
            return None
        if "@" in address:
            return email_to_pid.get(address.strip().lower())
        suffix = _phone_suffix(address)
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
        # Group raw rows by matched person_id.
        per_person_rows: dict[str, list[sqlite3.Row]] = {}

        try:
            rows = conn.execute(
                """
                SELECT
                    ZDATE        AS z_date,
                    ZDURATION    AS z_duration,
                    ZADDRESS     AS z_address,
                    ZORIGINATED  AS z_originated,
                    ZANSWERED    AS z_answered,
                    ZCALLTYPE    AS z_calltype
                FROM ZCALLRECORD
                """
            ).fetchall()
        except sqlite3.Error as e:
            log.warning("calls: ZCALLRECORD query failed: %s", e)
            return {}

        for row in rows:
            try:
                addr = _decode_address(row["z_address"])
            except Exception:
                continue
            if not addr:
                continue
            pid = self._match_address_to_pid(
                addr, phone_suffix_to_pid, email_to_pid
            )
            if not pid:
                continue
            per_person_rows.setdefault(pid, []).append(row)

        if not per_person_rows:
            return {}

        results: dict[str, PersonSignals] = {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for pid, pid_rows in per_person_rows.items():
            signal = self._build_voice_signal(pid_rows)
            if signal.total_calls == 0:
                continue

            info = person_index.get(pid) or {}
            ps = PersonSignals(
                person_id=pid,
                person_name=info.get("name", ""),
                extracted_at=now_iso,
                source_coverage=[self.name],
            )
            ps.voice.append(signal)
            results[pid] = ps

        return results

    # ── signal construction ──────────────────────────────────────────

    def _build_voice_signal(self, rows: list[sqlite3.Row]) -> VoiceSignal:
        sig = VoiceSignal(source=self.name)

        if not rows:
            return sig

        durations: list[float] = []
        hour_counts: dict[int, int] = {}
        buckets: dict[str, int] = {}
        first_date: float | None = None
        last_date: float | None = None

        for r in rows:
            sig.total_calls += 1

            try:
                z_date = float(r["z_date"] or 0)
            except (TypeError, ValueError):
                z_date = 0.0

            try:
                duration = float(r["z_duration"] or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if duration < 0:
                duration = 0.0
            durations.append(duration)

            originated = int(r["z_originated"] or 0)
            answered = int(r["z_answered"] or 0)
            if originated == 1:
                sig.outgoing += 1
            else:
                sig.incoming += 1
            if answered == 1:
                sig.answered_calls += 1
            elif originated == 0:
                # Incoming & not answered = missed.
                sig.missed_calls += 1

            call_type = int(r["z_calltype"] or 0)
            if call_type == CALL_TYPE_PHONE:
                sig.phone_calls += 1
            elif call_type == CALL_TYPE_FACETIME_AUDIO:
                sig.facetime_audio += 1
            elif call_type == CALL_TYPE_FACETIME_VIDEO:
                sig.facetime_video += 1

            if first_date is None or z_date < first_date:
                first_date = z_date
            if last_date is None or z_date > last_date:
                last_date = z_date

            try:
                dt = datetime.fromtimestamp(_core_data_to_unix(z_date), tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                dt = None
            if dt is not None:
                hr = dt.hour
                hour_counts[hr] = hour_counts.get(hr, 0) + 1
                bucket_key = f"{dt.year:04d}-{dt.month:02d}"
                buckets[bucket_key] = buckets.get(bucket_key, 0) + 1

        total_seconds = sum(durations)
        sig.total_minutes = total_seconds / 60.0
        if sig.total_calls > 0:
            sig.avg_duration_minutes = (total_seconds / sig.total_calls) / 60.0
        if durations:
            sig.max_duration_minutes = max(durations) / 60.0

        if first_date is not None:
            sig.first_call_date = _core_data_to_iso(first_date)
        if last_date is not None:
            sig.last_call_date = _core_data_to_iso(last_date)

        sig.time_of_day = hour_counts
        sig.temporal_buckets = buckets
        sig.temporal_pattern = _compute_temporal_pattern(buckets)

        if sig.total_calls > 0:
            sig.answer_rate = sig.answered_calls / sig.total_calls
        else:
            sig.answer_rate = 0.0

        return sig
