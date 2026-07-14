"""Telegram signal adapter.

Extracts interpersonal communication signals from the Telegram bridge JSONL
log at ``~/.aos/data/telegram-messages.jsonl``.

The bridge appends one JSON object per line. Each line describes a single
message flowing through the operator's Telegram bridge — both messages sent
by the operator (``from_me=true``) and messages received from third parties
(``from_me=false``). Today the file is dominated by operator-to-bridge
control traffic, but over time third-party messages will accumulate and this
adapter will surface them as per-person communication signals.

Line format (example)::

    {
      "id": "tg-1555",
      "chat_id": 6679471412,
      "from_user": {
          "id": 6679471412,
          "first_name": "Hi",
          "last_name": "Al",
          "username": null
      },
      "text": "hello there",
      "timestamp": 1774560364.0,
      "from_me": false,
      "thread_id": null
    }

Timestamps are unix seconds (float), **not** Apple epoch.

Matching strategy — try in order:

1. Telegram user id — if ``person_index[pid]`` has a ``telegram_ids`` list,
   match ``from_user.id`` (compared as a string).
2. Full name — ``"{first} {last}".strip().lower()`` matches
   ``person_index[pid]["name"].lower()``. Also try a space-split variant so
   camelCase canonical names (``SamTaylor``) match ``first="Sam" last="Taylor"``.
3. Username — ``from_user.username`` matches ``person_index[pid]
   ["telegram_usernames"]`` if that list is present.

Only inbound messages (``from_me=false``) that match a person contribute to
signals. Empty results, missing files, malformed JSON lines, and unexpected
shapes are all handled gracefully — the adapter never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Iterable

from ..types import (
    CommunicationSignal,
    PersonSignals,
    SignalType,
)
from .base import SignalAdapter

log = logging.getLogger(__name__)


DEFAULT_JSONL_PATH = "~/.aos/data/telegram-messages.jsonl"


def _classify_temporal(buckets: dict[str, int]) -> str:
    """Heuristic temporal pattern from YYYY-MM buckets.

    Mirrors the logic used by the WhatsApp adapter so that downstream
    consumers see comparable classifications across sources.
    """
    if not buckets:
        return "none"
    if len(buckets) == 1:
        total = next(iter(buckets.values()))
        return "one_shot" if total <= 1 else "clustered"

    ordered = sorted(buckets.items())
    counts = [c for _, c in ordered]

    if len(ordered) >= 3 and all(c >= 5 for c in counts):
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

    if any(c >= 10 for c in counts) and min(counts) <= 1:
        return "clustered"

    return "episodic"


def _name_variants(name: str | None) -> list[str]:
    """Return lowercase name variants for fuzzy full-name matching.

    The raw name and a space-split variant (inserting spaces at lower->upper
    capital boundaries) are both returned. This lets ``SamTaylor`` match a
    Telegram user with first_name ``Sam`` and last_name ``Taylor``.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return []
    variants = {cleaned.lower()}
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    if spaced != cleaned:
        variants.add(spaced.lower())
    return list(variants)


@dataclass
class _PersonBucket:
    """Mutable per-person aggregation scratch pad."""

    person_id: str
    person_name: str
    messages: list[dict] = field(default_factory=list)  # each: {text, ts}
    temporal_buckets: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    time_of_day: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    first_ts: float | None = None
    last_ts: float | None = None
    links: int = 0
    total_chars: int = 0
    non_empty_texts: int = 0


class TelegramAdapter(SignalAdapter):
    """Signal adapter for the Telegram bridge JSONL log."""

    name: ClassVar[str] = "telegram"
    display_name: ClassVar[str] = "Telegram"
    platform: ClassVar[str] = "any"
    signal_types: ClassVar[list[SignalType]] = [SignalType.COMMUNICATION]
    description: ClassVar[str] = (
        "Interpersonal messages from the Telegram bridge JSONL"
    )
    requires: ClassVar[list[str]] = ["file:~/.aos/data/telegram-messages.jsonl"]

    def __init__(self, jsonl_path: str | None = None):
        raw = jsonl_path or DEFAULT_JSONL_PATH
        self.jsonl_path: str = os.path.expanduser(raw)

    # ── Availability ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            return Path(self.jsonl_path).is_file()
        except Exception:  # noqa: BLE001 — contract: never raise
            return False

    # ── Extraction ────────────────────────────────────────────────────

    def extract_all(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        try:
            if not self.is_available():
                return {}

            # Build identifier lookups. Persons may or may not carry
            # telegram-specific fields — tolerate absence.
            id_lookup: dict[str, str] = {}                # telegram user id (str) → pid
            username_lookup: dict[str, str] = {}          # username (lower, no @) → pid
            name_lookup: dict[str, str] = {}              # name variant (lower) → pid

            for pid, info in (person_index or {}).items():
                if not isinstance(info, dict):
                    continue
                # Telegram user ids
                tg_ids = info.get("telegram_ids") or []
                if isinstance(tg_ids, (list, tuple)):
                    for tg_id in tg_ids:
                        if tg_id is None:
                            continue
                        id_lookup[str(tg_id)] = pid
                # Telegram usernames
                tg_usernames = info.get("telegram_usernames") or []
                if isinstance(tg_usernames, (list, tuple)):
                    for uname in tg_usernames:
                        if not uname:
                            continue
                        username_lookup[str(uname).lstrip("@").lower()] = pid
                # Name variants
                name = info.get("name")
                for variant in _name_variants(name):
                    # Don't clobber: first person wins on collision.
                    name_lookup.setdefault(variant, pid)

            buckets: dict[str, _PersonBucket] = {}

            with open(self.jsonl_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        log.debug(
                            "telegram: skipping malformed line: %s", exc
                        )
                        continue
                    try:
                        self._process_record(
                            record,
                            person_index=person_index,
                            id_lookup=id_lookup,
                            username_lookup=username_lookup,
                            name_lookup=name_lookup,
                            buckets=buckets,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "telegram: skipping bad record: %s", exc
                        )
                        continue

            return {
                pid: self._build_signals(bucket)
                for pid, bucket in buckets.items()
                if bucket.messages
            }
        except Exception as exc:  # noqa: BLE001 — contract: never raise
            log.warning("telegram: extract_all failed: %s", exc)
            return {}

    # ── Helpers ───────────────────────────────────────────────────────

    def _process_record(
        self,
        record: dict,
        *,
        person_index: dict[str, dict],
        id_lookup: dict[str, str],
        username_lookup: dict[str, str],
        name_lookup: dict[str, str],
        buckets: dict[str, _PersonBucket],
    ) -> None:
        if not isinstance(record, dict):
            return
        if record.get("from_me") is True:
            return
        from_user = record.get("from_user") or {}
        if not isinstance(from_user, dict):
            return

        pid = self._match_person(
            from_user,
            id_lookup=id_lookup,
            username_lookup=username_lookup,
            name_lookup=name_lookup,
        )
        if pid is None:
            return

        ts = record.get("timestamp")
        if not isinstance(ts, (int, float)):
            return
        ts = float(ts)

        text_raw = record.get("text")
        text = text_raw if isinstance(text_raw, str) else ""

        bucket = buckets.get(pid)
        if bucket is None:
            person_info = person_index.get(pid) or {}
            person_name = person_info.get("name", "") if isinstance(person_info, dict) else ""
            bucket = _PersonBucket(person_id=pid, person_name=person_name)
            buckets[pid] = bucket

        bucket.messages.append({"text": text, "ts": ts})
        if bucket.first_ts is None or ts < bucket.first_ts:
            bucket.first_ts = ts
        if bucket.last_ts is None or ts > bucket.last_ts:
            bucket.last_ts = ts

        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return
        bucket.temporal_buckets[f"{dt.year:04d}-{dt.month:02d}"] += 1
        bucket.time_of_day[dt.hour] += 1

        if text:
            bucket.non_empty_texts += 1
            bucket.total_chars += len(text)
            if "http" in text.lower():
                bucket.links += 1

    def _match_person(
        self,
        from_user: dict,
        *,
        id_lookup: dict[str, str],
        username_lookup: dict[str, str],
        name_lookup: dict[str, str],
    ) -> str | None:
        # 1. Telegram user id
        tg_id = from_user.get("id")
        if tg_id is not None:
            pid = id_lookup.get(str(tg_id))
            if pid:
                return pid

        # 2. Full name match (with space-split variant)
        first = (from_user.get("first_name") or "").strip()
        last = (from_user.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
        if full:
            lowered = full.lower()
            pid = name_lookup.get(lowered)
            if pid:
                return pid
            # Also try without spaces, to match concatenated canonical names
            collapsed = lowered.replace(" ", "")
            if collapsed and collapsed != lowered:
                pid = name_lookup.get(collapsed)
                if pid:
                    return pid

        # 3. Username
        username = from_user.get("username")
        if username:
            pid = username_lookup.get(str(username).lstrip("@").lower())
            if pid:
                return pid

        return None

    def _build_signals(self, bucket: _PersonBucket) -> PersonSignals:
        total = len(bucket.messages)

        # Sample messages: up to 5 most recent non-empty, by timestamp.
        non_empty = [m for m in bucket.messages if m["text"]]
        non_empty.sort(key=lambda m: m["ts"], reverse=True)
        sample_messages: list[dict] = []
        for m in non_empty[:5]:
            try:
                iso = datetime.fromtimestamp(m["ts"], tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                iso = None
            sample_messages.append(
                {
                    "text": m["text"],
                    "date": iso,
                    "direction": "received",
                    "channel": "telegram",
                }
            )

        avg_len = (
            bucket.total_chars / bucket.non_empty_texts
            if bucket.non_empty_texts
            else 0.0
        )

        first_iso = (
            datetime.fromtimestamp(bucket.first_ts, tz=timezone.utc).isoformat()
            if bucket.first_ts is not None
            else None
        )
        last_iso = (
            datetime.fromtimestamp(bucket.last_ts, tz=timezone.utc).isoformat()
            if bucket.last_ts is not None
            else None
        )

        # Time-of-day distribution percentages
        tod = dict(bucket.time_of_day)
        total_hours = sum(tod.values()) or 1
        late_night = sum(c for h, c in tod.items() if h >= 22 or h < 5)
        business = sum(c for h, c in tod.items() if 9 <= h < 17)
        evening = sum(c for h, c in tod.items() if 17 <= h < 22)

        temporal_buckets = dict(bucket.temporal_buckets)

        comm = CommunicationSignal(
            source="telegram",
            channel="telegram",
            total_messages=total,
            sent=0,
            received=total,
            first_message_date=first_iso,
            last_message_date=last_iso,
            temporal_buckets=temporal_buckets,
            temporal_pattern=_classify_temporal(temporal_buckets),
            avg_message_length=avg_len,
            response_latency_median=None,
            response_latency_avg=None,
            time_of_day=tod,
            late_night_pct=late_night / total_hours,
            business_hours_pct=business / total_hours,
            evening_pct=evening / total_hours,
            voice_notes_sent=0,
            voice_notes_received=0,
            media_sent=0,
            media_received=0,
            links_shared=bucket.links,
            reactions_given=0,
            reactions_received=0,
            service_breakdown={},
            sample_messages=sample_messages,
        )

        return PersonSignals(
            person_id=bucket.person_id,
            person_name=bucket.person_name,
            source_coverage=["telegram"],
            communication=[comm],
        )
