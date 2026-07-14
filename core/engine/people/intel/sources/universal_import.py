"""Universal message import adapter.

Reads JSONL files from ~/.aos/imports/ containing messages from any platform
(Instagram, Facebook Messenger, LinkedIn, Discord, Twitter, etc.) converted
to the AOS universal message format.

Each line in a .jsonl file is a JSON object with:
{
    "platform": "instagram",
    "conversation_id": "inbox_alice",
    "conversation_name": "Alice Smith",
    "sender": "alice_smith",
    "sender_display": "Alice Smith",
    "from_me": false,
    "timestamp": "2025-01-15T10:30:00Z",
    "text": "hey",
    "media_type": null
}

The adapter groups messages by sender, fuzzy-matches sender_display to
existing people (via person_index), and produces CommunicationSignal objects.

Platform-specific converters (Instagram ZIP → JSONL, Facebook ZIP → JSONL,
LinkedIn CSV → JSONL) are separate scripts that write to the imports dir.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from rapidfuzz import fuzz

from ..types import CommunicationSignal, PersonSignals, SignalType
from .base import SignalAdapter

logger = logging.getLogger(__name__)

_IMPORTS_DIR = Path.home() / ".aos" / "imports"


class UniversalImportAdapter(SignalAdapter):
    """Extract communication signals from universal JSONL import files."""

    name: ClassVar[str] = "universal_import"
    display_name: ClassVar[str] = "Universal Import"
    platform: ClassVar[str] = "any"
    signal_types: ClassVar[list[SignalType]] = [SignalType.COMMUNICATION]
    description: ClassVar[str] = "Import messages from any platform via JSONL files"
    requires: ClassVar[list[str]] = [f"dir:{_IMPORTS_DIR}"]

    def is_available(self) -> bool:
        if not _IMPORTS_DIR.exists():
            return False
        return any(_IMPORTS_DIR.glob("*.jsonl"))

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            return {}

        # Build name lookup for fuzzy matching
        name_to_pid: dict[str, str] = {}
        for pid, data in person_index.items():
            name = data.get("name", "").lower()
            if name:
                name_to_pid[name] = pid

        # Read all JSONL files
        all_messages: list[dict] = []
        for jsonl_file in sorted(_IMPORTS_DIR.glob("*.jsonl")):
            try:
                for line in jsonl_file.read_text().splitlines():
                    line = line.strip()
                    if line:
                        all_messages.append(json.loads(line))
            except Exception as e:
                logger.warning("Failed to read %s: %s", jsonl_file.name, e)

        if not all_messages:
            return {}

        # Group by sender (non-self messages only)
        by_sender: dict[str, list[dict]] = defaultdict(list)
        for msg in all_messages:
            if msg.get("from_me"):
                continue
            sender_key = (msg.get("sender_display") or msg.get("sender") or "").strip()
            if sender_key:
                by_sender[sender_key].append(msg)

        # Also track outbound messages per conversation for reciprocity
        outbound_by_conv: dict[str, int] = defaultdict(int)
        for msg in all_messages:
            if msg.get("from_me"):
                conv = msg.get("conversation_id", "")
                outbound_by_conv[conv] += 1

        # Match senders to people
        results: dict[str, PersonSignals] = {}

        for sender_name, messages in by_sender.items():
            # Try exact match
            pid = name_to_pid.get(sender_name.lower())

            # Try fuzzy match
            if not pid:
                best_score = 0
                best_pid = None
                for known_name, known_pid in name_to_pid.items():
                    score = fuzz.token_sort_ratio(sender_name.lower(), known_name)
                    if score > best_score and score >= 85:
                        best_score = score
                        best_pid = known_pid
                pid = best_pid

            if not pid:
                # Store as unresolved for later manual linking
                logger.debug("Unresolved import sender: %s (%d messages)", sender_name, len(messages))
                continue

            # Build signal
            platform = messages[0].get("platform", "import")
            total = len(messages)
            buckets: dict[str, int] = defaultdict(int)
            hour_counts: dict[int, int] = defaultdict(int)
            media_count = 0

            first_dt = None
            last_dt = None

            for msg in messages:
                ts_str = msg.get("timestamp")
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        buckets[dt.strftime("%Y-%m")] += 1
                        hour_counts[dt.hour] += 1
                        if first_dt is None or dt < first_dt:
                            first_dt = dt
                        if last_dt is None or dt > last_dt:
                            last_dt = dt
                    except ValueError:
                        pass

                if msg.get("media_type"):
                    media_count += 1

            # Count outbound for this person's conversations
            person_convs = {m.get("conversation_id") for m in messages}
            sent = sum(outbound_by_conv.get(c, 0) for c in person_convs)

            sig = CommunicationSignal(
                source="universal_import",
                channel=platform,
                total_messages=total + sent,
                sent=sent,
                received=total,
                first_message_date=first_dt.isoformat() if first_dt else None,
                last_message_date=last_dt.isoformat() if last_dt else None,
                temporal_buckets=dict(buckets),
                temporal_pattern=_detect_pattern(dict(buckets)),
                media_received=media_count,
                time_of_day=dict(hour_counts),
            )

            if total + sent > 0:
                late = sum(hour_counts.get(h, 0) for h in range(22, 24)) + sum(
                    hour_counts.get(h, 0) for h in range(0, 5)
                )
                business = sum(hour_counts.get(h, 0) for h in range(9, 17))
                evening = sum(hour_counts.get(h, 0) for h in range(17, 22))
                msg_total = total + sent
                sig.late_night_pct = round(late / msg_total, 3)
                sig.business_hours_pct = round(business / msg_total, 3)
                sig.evening_pct = round(evening / msg_total, 3)

            if pid in results:
                results[pid].communication.append(sig)
            else:
                ps = PersonSignals(
                    person_id=pid,
                    person_name=person_index[pid].get("name", ""),
                    source_coverage=["universal_import"],
                )
                ps.communication.append(sig)
                results[pid] = ps

        return results


def _detect_pattern(buckets: dict[str, int]) -> str:
    if not buckets:
        return "none"
    counts = list(buckets.values())
    if len(counts) <= 1:
        return "one_shot"
    if all(c > 0 for c in counts):
        return "consistent"
    return "episodic"
