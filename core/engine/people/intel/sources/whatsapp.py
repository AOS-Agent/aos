"""WhatsApp signal adapter.

Extracts communication + group membership signals from the WhatsApp for macOS
ChatStorage.sqlite database (Core Data / Z-prefixed schema).

Timestamps are in Apple / Core Data epoch (seconds since 2001-01-01 UTC).
We copy the database to a temp directory before reading to avoid SQLite
locking issues with the running WhatsApp app.

This adapter ONLY produces signals for persons already present in the
person_index passed to extract_all() — matching is done by exact wa_jid first,
then by phone-number suffix (last 10 digits) as a fallback.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ..types import (
    CommunicationSignal,
    GroupSignal,
    PersonSignals,
    SignalType,
)
from .base import SignalAdapter

logger = logging.getLogger(__name__)


# Apple / Core Data epoch starts at 2001-01-01. Unix epoch offset:
APPLE_EPOCH_OFFSET = 978307200

# WhatsApp Z* message-type constants (observed in ChatStorage.sqlite):
WA_MSG_TEXT = 0
WA_MSG_IMAGE = 1
WA_MSG_VOICE_NOTE = 2
WA_MSG_VIDEO = 3
WA_MSG_LOCATION = 5
WA_MSG_LINK = 7
WA_MSG_DOCUMENT = 8
WA_MSG_CONTACT = 14
WA_MSG_STICKER = 15

_MEDIA_TYPES = {WA_MSG_IMAGE, WA_MSG_VIDEO, WA_MSG_DOCUMENT, WA_MSG_STICKER}

_DEFAULT_DB_PATH = (
    "~/Library/Group Containers/"
    "group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
)

_GROUP_KEYWORDS: dict[str, list[str]] = {
    "religious": ["masjid", "mosque", "quran", "islam", "halaqa"],
    "family": ["family", "cousin", "sibling", "parent"],
    "work": ["work", "team", "office", "project"],
    "social": ["friend", "crew", "gang"],
}


def _apple_to_unix(mac_ts: float | None) -> float | None:
    if mac_ts is None:
        return None
    try:
        return float(mac_ts) + APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return None


def _unix_to_iso(unix_ts: float | None) -> str | None:
    if unix_ts is None:
        return None
    try:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _phone_suffix(phone: str) -> str | None:
    """Return the last 10 digits of a phone number, or None if too short."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return digits or None
    return digits[-10:]


def _jid_digits(jid: str) -> str:
    """Extract the digits portion from a WhatsApp JID like '14155550123@s.whatsapp.net'."""
    if "@" not in jid:
        return re.sub(r"\D", "", jid or "")
    local = jid.split("@", 1)[0]
    return re.sub(r"\D", "", local)


def _classify_temporal(buckets: dict[str, int]) -> str:
    """Heuristic temporal pattern from YYYY-MM buckets."""
    if not buckets:
        return "none"
    if len(buckets) == 1:
        total = next(iter(buckets.values()))
        return "one_shot" if total <= 1 else "clustered"

    ordered = sorted(buckets.items())
    counts = [c for _, c in ordered]

    if len(ordered) >= 3 and all(c >= 5 for c in counts):
        # No empty gap months between first and last
        return "consistent"

    if len(ordered) >= 6:
        first_three = counts[:3]
        last_three = counts[-3:]
        first_avg = sum(first_three) / 3
        last_avg = sum(last_three) / 3
        if first_avg > 0 and last_avg > first_avg * 2:
            return "growing"
        if last_avg > 0 and first_avg > last_avg * 2:
            return "fading"

    # Check for clustering (bursts of activity followed by gaps)
    if any(c >= 10 for c in counts) and min(counts) <= 1:
        return "clustered"

    return "episodic"


def _categorize_group(name: str) -> list[str]:
    lowered = (name or "").lower()
    hits: list[str] = []
    for category, keywords in _GROUP_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            hits.append(category)
    return hits


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {r[1] for r in rows}


class WhatsAppAdapter(SignalAdapter):
    """Signal adapter for WhatsApp macOS ChatStorage.sqlite."""

    name: ClassVar[str] = "whatsapp"
    display_name: ClassVar[str] = "WhatsApp"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [
        SignalType.COMMUNICATION,
        SignalType.GROUP_MEMBERSHIP,
    ]
    description: ClassVar[str] = "WhatsApp messages, voice notes, media, groups"
    requires: ClassVar[list[str]] = [
        "file:~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
    ]

    def __init__(self, db_path: str | os.PathLike | None = None) -> None:
        raw = str(db_path) if db_path is not None else _DEFAULT_DB_PATH
        self.db_path = Path(os.path.expanduser(raw))

    # ── Availability ───────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            return self.db_path.exists() and self.db_path.is_file()
        except Exception:  # noqa: BLE001
            return False

    # ── Extraction ─────────────────────────────────────────────────────

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            logger.info("whatsapp adapter unavailable: %s", self.db_path)
            return {}

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_db = Path(tmpdir) / "ChatStorage.sqlite"
                try:
                    shutil.copy2(self.db_path, tmp_db)
                except Exception as e:  # noqa: BLE001
                    logger.warning("whatsapp: failed to copy db: %s", e)
                    return {}
                return self._extract_from_copy(tmp_db, person_index)
        except Exception as e:  # noqa: BLE001
            logger.exception("whatsapp: catastrophic extraction failure: %s", e)
            return {}

    # ── Internal ───────────────────────────────────────────────────────

    def _extract_from_copy(
        self, db_file: Path, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row

            # Build lookup structures
            jid_to_pid: dict[str, str] = {}
            phone_suffix_to_pid: dict[str, str] = {}
            names: dict[str, str] = {}

            for pid, info in person_index.items():
                names[pid] = info.get("name") or ""
                for jid in info.get("wa_jids") or []:
                    if jid:
                        jid_to_pid[jid] = pid
                for phone in info.get("phones") or []:
                    suf = _phone_suffix(phone)
                    if suf and suf not in phone_suffix_to_pid:
                        phone_suffix_to_pid[suf] = pid

            def match_jid(jid: str | None) -> str | None:
                if not jid:
                    return None
                if jid in jid_to_pid:
                    return jid_to_pid[jid]
                digits = _jid_digits(jid)
                if len(digits) >= 10:
                    return phone_suffix_to_pid.get(digits[-10:])
                return phone_suffix_to_pid.get(digits) if digits else None

            # Sanity check required tables
            if not _table_exists(conn, "ZWAMESSAGE") or not _table_exists(
                conn, "ZWACHATSESSION"
            ):
                logger.warning("whatsapp: required tables missing")
                return {}

            msg_cols = _column_names(conn, "ZWAMESSAGE")
            has_text = "ZTEXT" in msg_cols

            # ── Individual chats ──
            # ZSESSIONTYPE = 0 is individual (1-to-1)
            indiv_query = f"""
                SELECT m.ZMESSAGEDATE   AS mdate,
                       m.ZMESSAGETYPE   AS mtype,
                       m.ZISFROMME      AS fromme,
                       {"m.ZTEXT" if has_text else "NULL"} AS text,
                       s.ZCONTACTJID    AS jid
                  FROM ZWAMESSAGE m
                  JOIN ZWACHATSESSION s ON m.ZCHATSESSION = s.Z_PK
                 WHERE s.ZSESSIONTYPE = 0
            """

            # Per-person accumulator
            per_person: dict[str, dict] = defaultdict(
                lambda: {
                    "messages": [],  # list of dicts
                    "jid": None,
                }
            )

            for row in conn.execute(indiv_query):
                jid = row["jid"]
                pid = match_jid(jid)
                if pid is None:
                    continue
                per_person[pid]["jid"] = jid
                per_person[pid]["messages"].append(
                    {
                        "mdate": row["mdate"],
                        "mtype": row["mtype"],
                        "fromme": row["fromme"],
                        "text": row["text"],
                    }
                )

            results: dict[str, PersonSignals] = {}
            extracted_at = datetime.now(timezone.utc).isoformat()

            for pid, bundle in per_person.items():
                comm = self._build_communication(bundle["messages"])
                ps = PersonSignals(
                    person_id=pid,
                    person_name=names.get(pid, ""),
                    extracted_at=extracted_at,
                    source_coverage=["whatsapp"],
                )
                ps.communication.append(comm)
                results[pid] = ps

            # ── Group memberships ──
            if _table_exists(conn, "ZWAGROUPMEMBER"):
                group_query = """
                    SELECT gm.ZMEMBERJID AS member_jid,
                           s.Z_PK        AS session_pk,
                           s.ZCONTACTJID AS group_jid,
                           s.ZPARTNERNAME AS group_name
                      FROM ZWAGROUPMEMBER gm
                      JOIN ZWACHATSESSION s ON gm.ZCHATSESSION = s.Z_PK
                     WHERE s.ZSESSIONTYPE = 1
                """
                # group_pk -> {name, members: set of member_jids}
                groups_info: dict[int, dict] = {}
                # person_id -> list of group_pks
                pid_groups: dict[str, list[int]] = defaultdict(list)

                for row in conn.execute(group_query):
                    gpk = row["session_pk"]
                    gname = row["group_name"] or "Unknown"
                    info = groups_info.setdefault(
                        gpk, {"name": gname, "members": set()}
                    )
                    if row["member_jid"]:
                        info["members"].add(row["member_jid"])

                    pid = match_jid(row["member_jid"])
                    if pid is not None and gpk not in pid_groups[pid]:
                        pid_groups[pid].append(gpk)

                for pid, gpk_list in pid_groups.items():
                    if not gpk_list:
                        continue
                    groups_payload: list[dict] = []
                    category_counts: dict[str, int] = defaultdict(int)
                    for gpk in gpk_list:
                        ginfo = groups_info.get(gpk, {})
                        gname = ginfo.get("name", "Unknown")
                        member_count = len(ginfo.get("members", set()))
                        groups_payload.append(
                            {
                                "name": gname,
                                "type": "whatsapp",
                                "member_count": member_count,
                                "role": "member",
                            }
                        )
                        for cat in _categorize_group(gname):
                            category_counts[cat] += 1

                    gsig = GroupSignal(
                        source="whatsapp",
                        groups=groups_payload,
                        total_groups=len(groups_payload),
                        shared_with_operator=len(groups_payload),
                        group_categories=dict(category_counts),
                    )

                    if pid in results:
                        results[pid].group_membership.append(gsig)
                    else:
                        ps = PersonSignals(
                            person_id=pid,
                            person_name=names.get(pid, ""),
                            extracted_at=extracted_at,
                            source_coverage=["whatsapp"],
                        )
                        ps.group_membership.append(gsig)
                        results[pid] = ps

            return results
        except Exception as e:  # noqa: BLE001
            logger.exception("whatsapp: extraction error: %s", e)
            return {}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def _build_communication(self, messages: list[dict]) -> CommunicationSignal:
        sig = CommunicationSignal(source="whatsapp", channel="whatsapp")

        if not messages:
            return sig

        # Sort by date ascending for temporal + latency
        sorted_msgs = sorted(
            messages, key=lambda m: m.get("mdate") if m.get("mdate") is not None else 0
        )

        text_lengths: list[int] = []
        hour_counts: dict[int, int] = defaultdict(int)
        bucket_counts: dict[str, int] = defaultdict(int)
        first_unix: float | None = None
        last_unix: float | None = None
        latencies_minutes: list[float] = []
        prev_dir: int | None = None
        prev_unix: float | None = None

        late_night = 0
        business_hours = 0
        evening = 0
        total = 0
        sent = 0
        received = 0
        voice_sent = 0
        voice_recv = 0
        media_sent = 0
        media_recv = 0
        links = 0

        # Samples: last 5 non-empty text messages
        sample_buf: list[dict] = []

        for m in sorted_msgs:
            mdate = m.get("mdate")
            mtype = m.get("mtype")
            fromme = 1 if m.get("fromme") else 0
            text = m.get("text")

            unix_ts = _apple_to_unix(mdate)
            if unix_ts is None:
                continue

            total += 1
            if fromme:
                sent += 1
            else:
                received += 1

            if first_unix is None or unix_ts < first_unix:
                first_unix = unix_ts
            if last_unix is None or unix_ts > last_unix:
                last_unix = unix_ts

            try:
                dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue

            hour = dt.hour
            hour_counts[hour] += 1
            bucket_counts[dt.strftime("%Y-%m")] += 1

            if hour >= 22 or hour < 5:
                late_night += 1
            if 9 <= hour < 17:
                business_hours += 1
            if 17 <= hour < 22:
                evening += 1

            # Latency on direction flip
            if prev_dir is not None and prev_unix is not None and fromme != prev_dir:
                delta_min = (unix_ts - prev_unix) / 60.0
                if delta_min >= 0:
                    latencies_minutes.append(delta_min)
            prev_dir = fromme
            prev_unix = unix_ts

            # Text length (text messages only)
            if mtype == WA_MSG_TEXT and text:
                text_lengths.append(len(text))
                sample_buf.append(
                    {
                        "text": text,
                        "date": _unix_to_iso(unix_ts),
                        "direction": "sent" if fromme else "received",
                        "channel": "whatsapp",
                    }
                )

            # Voice notes
            if mtype == WA_MSG_VOICE_NOTE:
                if fromme:
                    voice_sent += 1
                else:
                    voice_recv += 1

            # Media: images, videos, docs, stickers
            if mtype in _MEDIA_TYPES:
                if fromme:
                    media_sent += 1
                else:
                    media_recv += 1

            # Links: explicit link type OR http(s) in text
            is_link = mtype == WA_MSG_LINK
            if not is_link and text and ("http://" in text or "https://" in text):
                is_link = True
            if is_link:
                links += 1

        sig.total_messages = total
        sig.sent = sent
        sig.received = received
        sig.first_message_date = _unix_to_iso(first_unix)
        sig.last_message_date = _unix_to_iso(last_unix)
        sig.temporal_buckets = dict(bucket_counts)
        sig.temporal_pattern = _classify_temporal(sig.temporal_buckets)
        if text_lengths:
            sig.avg_message_length = sum(text_lengths) / len(text_lengths)
        if latencies_minutes:
            sig.response_latency_median = statistics.median(latencies_minutes)
            sig.response_latency_avg = statistics.mean(latencies_minutes)
        sig.time_of_day = dict(hour_counts)
        if total:
            sig.late_night_pct = late_night / total
            sig.business_hours_pct = business_hours / total
            sig.evening_pct = evening / total
        sig.voice_notes_sent = voice_sent
        sig.voice_notes_received = voice_recv
        sig.media_sent = media_sent
        sig.media_received = media_recv
        sig.links_shared = links
        sig.reactions_given = 0
        sig.reactions_received = 0
        sig.service_breakdown = {}
        sig.sample_messages = sample_buf[-5:]

        return sig
