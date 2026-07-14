"""Signal Desktop signal adapter.

Reads the Signal Desktop SQLite database (encrypted with SQLCipher) to
extract communication signals. The encryption key is stored in the Signal
Desktop config.json file in plaintext — same user, same machine.

Requirements:
    - Signal Desktop installed at ~/Library/Application Support/Signal/
    - pysqlcipher3 installed (pip install pysqlcipher3)
    - sqlcipher brew formula (brew install sqlcipher)

If Signal Desktop is not installed, is_available() returns False and the
adapter is silently skipped.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ..types import CommunicationSignal, PersonSignals, SignalType
from .base import SignalAdapter

logger = logging.getLogger(__name__)

_SIGNAL_DIR = Path.home() / "Library" / "Application Support" / "Signal"
_DB_PATH = _SIGNAL_DIR / "sql" / "db.sqlite"
_CONFIG_PATH = _SIGNAL_DIR / "config.json"


class SignalDesktopAdapter(SignalAdapter):
    """Extract communication signals from Signal Desktop's local database."""

    name: ClassVar[str] = "signal_desktop"
    display_name: ClassVar[str] = "Signal Desktop"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [SignalType.COMMUNICATION]
    description: ClassVar[str] = "Signal Desktop encrypted message database"
    requires: ClassVar[list[str]] = [
        f"file:{_DB_PATH}",
        f"file:{_CONFIG_PATH}",
        "pip:pysqlcipher3",
    ]

    def is_available(self) -> bool:
        if not _DB_PATH.exists() or not _CONFIG_PATH.exists():
            return False
        try:
            import pysqlcipher3  # noqa: F401
            return True
        except ImportError:
            logger.debug("pysqlcipher3 not installed — Signal Desktop adapter unavailable")
            return False

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            return {}

        import pysqlcipher3.dbapi2 as sqlcipher

        # Read encryption key from config.json
        try:
            config = json.loads(_CONFIG_PATH.read_text())
            key = config.get("key")
            if not key:
                logger.warning("No 'key' field in Signal config.json")
                return {}
        except Exception as e:
            logger.warning("Failed to read Signal config: %s", e)
            return {}

        # Copy DB to temp to avoid locking
        tmp = tempfile.mkdtemp(prefix="signal_extract_")
        tmp_db = Path(tmp) / "db.sqlite"
        try:
            shutil.copy2(str(_DB_PATH), str(tmp_db))
        except Exception as e:
            logger.warning("Failed to copy Signal DB: %s", e)
            return {}

        try:
            conn = sqlcipher.connect(str(tmp_db))
            conn.execute(f"PRAGMA key = \"x'{key}'\"")
            conn.execute("PRAGMA cipher_compatibility = 4")
            conn.row_factory = lambda c, r: dict(zip([d[0] for d in c.description], r))

            return self._extract(conn, person_index)
        except Exception as e:
            logger.warning("Signal DB extraction failed: %s", e)
            return {}
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def _extract(self, conn, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        # Build phone -> person_id lookup
        phone_to_pid: dict[str, str] = {}
        for pid, data in person_index.items():
            for phone in data.get("phones", []):
                # Normalize: strip +, spaces, dashes for suffix matching
                digits = "".join(c for c in phone if c.isdigit())
                if len(digits) >= 8:
                    phone_to_pid[digits[-10:]] = pid

        # Get all conversations
        try:
            conversations = conn.execute(
                "SELECT id, e164, name, type, profileName FROM conversations"
            ).fetchall()
        except Exception:
            logger.debug("Could not query Signal conversations table")
            return {}

        # Map conversation ID -> person_id
        conv_to_pid: dict[str, str] = {}
        for conv in conversations:
            if conv.get("type") != "private":
                continue
            e164 = conv.get("e164", "")
            if e164:
                digits = "".join(c for c in e164 if c.isdigit())
                if len(digits) >= 8:
                    pid = phone_to_pid.get(digits[-10:])
                    if pid:
                        conv_to_pid[conv["id"]] = pid

        if not conv_to_pid:
            return {}

        # Get messages per conversation
        results: dict[str, PersonSignals] = {}
        our_number = None  # Will detect from outbound messages

        for conv_id, pid in conv_to_pid.items():
            try:
                messages = conn.execute(
                    "SELECT sent_at, type, hasAttachments, body "
                    "FROM messages WHERE conversationId = ? ORDER BY sent_at",
                    (conv_id,),
                ).fetchall()
            except Exception:
                continue

            if not messages:
                continue

            sent = 0
            received = 0
            total = len(messages)
            buckets: dict[str, int] = defaultdict(int)
            hour_counts: dict[int, int] = defaultdict(int)
            media_count = 0

            for msg in messages:
                ts = msg.get("sent_at", 0)
                if ts:
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    buckets[dt.strftime("%Y-%m")] += 1
                    hour_counts[dt.hour] += 1

                msg_type = msg.get("type", "")
                if msg_type == "outgoing":
                    sent += 1
                else:
                    received += 1

                if msg.get("hasAttachments"):
                    media_count += 1

            first_ts = messages[0].get("sent_at", 0)
            last_ts = messages[-1].get("sent_at", 0)

            sig = CommunicationSignal(
                source="signal_desktop",
                channel="signal",
                total_messages=total,
                sent=sent,
                received=received,
                first_message_date=datetime.fromtimestamp(
                    first_ts / 1000, tz=timezone.utc
                ).isoformat() if first_ts else None,
                last_message_date=datetime.fromtimestamp(
                    last_ts / 1000, tz=timezone.utc
                ).isoformat() if last_ts else None,
                temporal_buckets=dict(buckets),
                temporal_pattern=self._detect_pattern(buckets),
                media_sent=media_count if sent > received else 0,
                media_received=media_count if received >= sent else media_count,
                time_of_day=dict(hour_counts),
            )

            # Compute time-of-day percentages
            if total > 0:
                late = sum(hour_counts.get(h, 0) for h in range(22, 24)) + sum(
                    hour_counts.get(h, 0) for h in range(0, 5)
                )
                business = sum(hour_counts.get(h, 0) for h in range(9, 17))
                evening = sum(hour_counts.get(h, 0) for h in range(17, 22))
                sig.late_night_pct = round(late / total, 3)
                sig.business_hours_pct = round(business / total, 3)
                sig.evening_pct = round(evening / total, 3)

            ps = PersonSignals(
                person_id=pid,
                person_name=person_index[pid].get("name", ""),
                source_coverage=["signal_desktop"],
            )
            ps.communication.append(sig)
            results[pid] = ps

        return results

    @staticmethod
    def _detect_pattern(buckets: dict[str, int]) -> str:
        if not buckets:
            return "none"
        counts = list(buckets.values())
        if len(counts) <= 1:
            return "one_shot"
        if all(c > 0 for c in counts):
            return "consistent"
        return "episodic"
