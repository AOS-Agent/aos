"""Apple Mail signal adapter.

Extracts email exchange signals from the Apple Mail Envelope Index
SQLite database at ~/Library/Mail/V*/MailData/Envelope Index.

The adapter:
  * Picks the highest-numbered V directory when db_path is not given
    (e.g., V10 beats V9).
  * Copies the Envelope Index to a temp directory before reading.
  * Detects whether an `addresses` table exists separately from
    `senders` (varies by macOS version) and adapts recipient lookup.
  * Detects sent-vs-received direction by mailbox URL ("Sent"/"Drafts").
  * Excludes automated senders (noreply, newsletters, mailer-daemon,
    etc.) entirely from per-person counts.
  * Matches senders/recipients to person_ids via lowercase email.
  * Aggregates per-person professional signals (volume, temporal,
    bidirectional ratio, subject keywords/categories, thread count,
    and thread depth).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ..types import PersonSignals, ProfessionalSignal, SignalType
from .base import SignalAdapter

log = logging.getLogger(__name__)


# Regex for identifying automated senders. Matches anywhere in the address.
AUTOMATED_SENDER_RE = re.compile(
    r"noreply|no-reply|notification|mailer-daemon|newsletter|unsubscribe|donotreply|postmaster",
    re.IGNORECASE,
)


# Simple stopword list for subject keyword extraction.
SUBJECT_STOPWORDS: set[str] = {
    "the", "a", "an", "is", "for", "and", "to", "of", "in",
    "re", "fw", "fwd", "hello", "hi", "hey",
    "on", "at", "by", "as", "or", "if", "it", "be", "you", "your",
    "from", "with", "this", "that", "was", "are", "our", "my",
}


# Subject category keywords. Checked in order: transactional, professional, personal.
TRANSACTIONAL_KEYWORDS = {
    "order", "receipt", "invoice", "payment", "shipped",
    "delivery", "confirmation", "booking", "reservation",
}
PROFESSIONAL_KEYWORDS = {
    "meeting", "project", "review", "proposal", "agenda",
    "schedule", "deadline", "update", "report", "sync",
}


_WORD_RE = re.compile(r"\w+")


def _classify_subject(subject: str | None) -> str:
    """Return 'transactional' | 'professional' | 'personal'."""
    if not subject:
        return "personal"
    words = {w.lower() for w in _WORD_RE.findall(subject)}
    if words & TRANSACTIONAL_KEYWORDS:
        return "transactional"
    if words & PROFESSIONAL_KEYWORDS:
        return "professional"
    return "personal"


def _unix_to_iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _compute_temporal_pattern(buckets: dict[str, int]) -> str:
    """Classify temporal_pattern from YYYY-MM buckets.

    Same heuristic as the other intel adapters.
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


def _find_highest_v_db(mail_root: Path) -> Path | None:
    """Find the Envelope Index in the highest-numbered V directory under mail_root."""
    if not mail_root.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for child in mail_root.iterdir():
        name = child.name
        if not name.startswith("V"):
            continue
        try:
            v_num = int(name[1:])
        except ValueError:
            continue
        db = child / "MailData" / "Envelope Index"
        if db.exists():
            candidates.append((v_num, db))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


class AppleMailAdapter(SignalAdapter):
    """Signal adapter for Apple Mail (Envelope Index)."""

    name: ClassVar[str] = "apple_mail"
    display_name: ClassVar[str] = "Apple Mail"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [SignalType.PROFESSIONAL]
    description: ClassVar[str] = "Email exchange patterns via Apple Mail Envelope Index"
    requires: ClassVar[list[str]] = ["file:~/Library/Mail/V*/MailData/Envelope Index"]

    def __init__(
        self,
        db_path: str | Path | None = None,
        mail_root: str | Path | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            db_path: Explicit path to an Envelope Index file. Overrides
                discovery if given.
            mail_root: Root Mail directory to search for V* subdirectories.
                Defaults to ~/Library/Mail. Used when db_path is None.
        """
        self._mail_root = Path(mail_root) if mail_root else (Path.home() / "Library" / "Mail")
        if db_path:
            self.db_path: Path | None = Path(db_path)
        else:
            self.db_path = self._find_db()

    def _find_db(self) -> Path | None:
        return _find_highest_v_db(self._mail_root)

    # ── availability ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            if self.db_path is None:
                return False
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
            log.exception("apple_mail extract_all failed: %s", e)
            return {}

    def _extract_all_inner(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        assert self.db_path is not None

        # Build email → pid lookup. Lowercased, stripped.
        email_to_pid: dict[str, str] = {}
        for pid, info in person_index.items():
            for email in info.get("emails") or []:
                if not email:
                    continue
                key = email.strip().lower()
                if not key:
                    continue
                existing = email_to_pid.get(key)
                if existing and existing != pid:
                    # Collision — drop the conflicting entry.
                    email_to_pid.pop(key, None)
                else:
                    email_to_pid[key] = pid

        if not email_to_pid:
            return {}

        with tempfile.TemporaryDirectory(prefix="aos-intel-mail-") as tmpdir:
            tmp_db = Path(tmpdir) / "Envelope Index"
            try:
                shutil.copy2(self.db_path, tmp_db)
            except Exception as e:
                log.warning("apple_mail: could not copy Envelope Index: %s", e)
                return {}

            try:
                conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            except sqlite3.Error as e:
                log.warning("apple_mail: could not open Envelope Index: %s", e)
                return {}

            try:
                conn.row_factory = sqlite3.Row
                return self._scan_and_group(conn, person_index, email_to_pid)
            finally:
                conn.close()

    # ── scan ──────────────────────────────────────────────────────────

    def _has_table(self, conn: sqlite3.Connection, name: str) -> bool:
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def _scan_and_group(
        self,
        conn: sqlite3.Connection,
        person_index: dict[str, dict],
        email_to_pid: dict[str, str],
    ) -> dict[str, PersonSignals]:
        # Detect separate addresses table.
        has_addresses_table = self._has_table(conn, "addresses")

        # Load senders.
        #
        # On modern macOS (V10+) the `senders` table holds no email text
        # at all — just `contact_identifier`, `bucket`, `user_initiated`.
        # Messages instead point at `addresses` directly via `messages.sender`.
        # On legacy versions `senders.address` exists. Try the legacy query
        # first; if it fails with a column error, fall back to empty and
        # rely on `addresses` for everything.
        sender_rowid_to_address: dict[int, str] = {}
        try:
            sender_rows = conn.execute("SELECT ROWID, address FROM senders").fetchall()
            for r in sender_rows:
                addr = (r["address"] or "").strip().lower()
                sender_rowid_to_address[int(r["ROWID"])] = addr
        except sqlite3.Error as e:
            log.debug("apple_mail: senders has no address column (modern schema): %s", e)

        # Load addresses table if present.
        addr_rowid_to_address: dict[int, str] = {}
        if has_addresses_table:
            try:
                addr_rows = conn.execute(
                    "SELECT ROWID, address FROM addresses"
                ).fetchall()
                for r in addr_rows:
                    addr = (r["address"] or "").strip().lower()
                    addr_rowid_to_address[int(r["ROWID"])] = addr
            except sqlite3.Error as e:
                log.warning("apple_mail: addresses query failed: %s", e)

        # If we have neither senders-with-address nor addresses table,
        # there's nothing to match — abort this run for this source.
        if not sender_rowid_to_address and not addr_rowid_to_address:
            log.warning("apple_mail: no address text found in either senders or addresses")
            return {}

        # Load mailboxes to detect sent/drafts.
        try:
            mbox_rows = conn.execute("SELECT ROWID, url FROM mailboxes").fetchall()
        except sqlite3.Error as e:
            log.warning("apple_mail: mailboxes query failed: %s", e)
            return {}

        mailbox_is_sent: dict[int, bool] = {}
        for r in mbox_rows:
            url = (r["url"] or "").lower()
            mailbox_is_sent[int(r["ROWID"])] = ("sent" in url) or ("drafts" in url)

        # Load subjects.
        try:
            subj_rows = conn.execute(
                "SELECT ROWID, subject FROM subjects"
            ).fetchall()
        except sqlite3.Error as e:
            log.warning("apple_mail: subjects query failed: %s", e)
            subj_rows = []

        subject_lookup: dict[int, str] = {}
        for r in subj_rows:
            subject_lookup[int(r["ROWID"])] = r["subject"] or ""

        # Load messages.
        try:
            msg_rows = conn.execute(
                """
                SELECT ROWID, date_sent, date_received, sender, subject,
                       mailbox, conversation_id
                FROM messages
                """
            ).fetchall()
        except sqlite3.Error as e:
            log.warning("apple_mail: messages query failed: %s", e)
            return {}

        # Load recipients (best-effort).
        #
        # Column names vary across macOS versions:
        #   legacy: recipients(message_id, address_id, type, ...)
        #   modern: recipients(message, address, type, position)
        # The modern schema doesn't use "_id" suffixes. Try modern first,
        # fall back to legacy — both forms are read-only and harmless.
        message_to_recipients: dict[int, list[str]] = {}

        def _collect_recipients(sql: str, msg_col: str, addr_col: str) -> bool:
            try:
                rcp_rows = conn.execute(sql).fetchall()
            except sqlite3.Error as e:
                log.debug("apple_mail: recipients query %r failed: %s", sql, e)
                return False
            for r in rcp_rows:
                try:
                    mid = int(r[msg_col])
                    aid = int(r[addr_col])
                except (KeyError, TypeError, ValueError):
                    continue
                if has_addresses_table and aid in addr_rowid_to_address:
                    addr = addr_rowid_to_address[aid]
                elif aid in sender_rowid_to_address:
                    addr = sender_rowid_to_address[aid]
                else:
                    continue
                if not addr:
                    continue
                message_to_recipients.setdefault(mid, []).append(addr)
            return True

        # Modern schema first
        if not _collect_recipients(
            "SELECT message, address FROM recipients", "message", "address"
        ):
            # Legacy fallback
            _collect_recipients(
                "SELECT message_id, address_id FROM recipients",
                "message_id",
                "address_id",
            )

        # Accumulator per person.
        # { pid: {
        #     "sent_to_you": int, "you_sent": int,
        #     "first_ts": float|None, "last_ts": float|None,
        #     "buckets": {YYYY-MM: int},
        #     "subjects": [str],           # subjects of all messages with person
        #     "categories": Counter,
        #     "conversation_ids": set[int],
        #     "message_ids_by_conv": dict[int, set[int]] - tracks messages seen per conv
        #   }
        # }
        per_pid: dict[str, dict] = {}

        # Also track, for thread-depth calc, per-conversation total message counts
        # across ALL messages (not just person-matched), so thread depth reflects
        # the true thread size.
        conversation_sizes: dict[int, int] = {}
        for row in msg_rows:
            cid = row["conversation_id"]
            if cid is None:
                continue
            conversation_sizes[int(cid)] = conversation_sizes.get(int(cid), 0) + 1

        def _bump(pid: str, role: str, ts: float | None, subject: str, conv_id: int | None):
            entry = per_pid.setdefault(
                pid,
                {
                    "sent_to_you": 0,
                    "you_sent": 0,
                    "first_ts": None,
                    "last_ts": None,
                    "buckets": {},
                    "subjects": [],
                    "categories": Counter(),
                    "conversations": set(),
                },
            )
            if role == "sent_to_you":
                entry["sent_to_you"] += 1
            elif role == "you_sent":
                entry["you_sent"] += 1

            if ts is not None:
                if entry["first_ts"] is None or ts < entry["first_ts"]:
                    entry["first_ts"] = ts
                if entry["last_ts"] is None or ts > entry["last_ts"]:
                    entry["last_ts"] = ts
                try:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    key = f"{dt.year:04d}-{dt.month:02d}"
                    entry["buckets"][key] = entry["buckets"].get(key, 0) + 1
                except (ValueError, OSError, OverflowError):
                    pass

            entry["subjects"].append(subject or "")
            cat = _classify_subject(subject)
            entry["categories"][cat] += 1
            if conv_id is not None:
                entry["conversations"].add(int(conv_id))

        for m in msg_rows:
            try:
                sender_id = m["sender"]
                sender_addr = sender_rowid_to_address.get(
                    int(sender_id) if sender_id is not None else -1, ""
                )

                # Exclude automated senders ENTIRELY.
                if sender_addr and AUTOMATED_SENDER_RE.search(sender_addr):
                    continue

                mbox_id = m["mailbox"]
                is_sent_mbox = mailbox_is_sent.get(
                    int(mbox_id) if mbox_id is not None else -1, False
                )

                # Timestamp preference: date_sent, fall back to date_received.
                ts_raw = m["date_sent"] or m["date_received"]
                ts: float | None = float(ts_raw) if ts_raw else None

                subj_id = m["subject"]
                subject = (
                    subject_lookup.get(int(subj_id), "") if subj_id is not None else ""
                )

                conv_id = m["conversation_id"]

                msg_rowid = int(m["ROWID"])

                if is_sent_mbox:
                    # You → recipients.
                    recipients = message_to_recipients.get(msg_rowid, [])
                    for addr in recipients:
                        if not addr:
                            continue
                        if AUTOMATED_SENDER_RE.search(addr):
                            continue
                        pid = email_to_pid.get(addr)
                        if pid is None:
                            continue
                        _bump(pid, "you_sent", ts, subject, conv_id)
                else:
                    # Received — attribute to sender.
                    if not sender_addr:
                        continue
                    pid = email_to_pid.get(sender_addr)
                    if pid is None:
                        continue
                    _bump(pid, "sent_to_you", ts, subject, conv_id)
            except Exception as e:  # pragma: no cover - per-row defense
                log.debug("apple_mail: row skipped: %s", e)
                continue

        # Build PersonSignals per matched person.
        results: dict[str, PersonSignals] = {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for pid, entry in per_pid.items():
            sent_to_you = entry["sent_to_you"]
            you_sent = entry["you_sent"]
            total = sent_to_you + you_sent
            if total == 0:
                continue

            if sent_to_you and you_sent:
                ratio = min(sent_to_you, you_sent) / max(sent_to_you, you_sent)
            else:
                ratio = 0.0

            # Subject keywords.
            keyword_counter: Counter[str] = Counter()
            for s in entry["subjects"]:
                if not s:
                    continue
                for word in _WORD_RE.findall(s.lower()):
                    if len(word) < 3:
                        continue
                    if word in SUBJECT_STOPWORDS:
                        continue
                    if not word.isalnum():
                        continue
                    keyword_counter[word] += 1
            top_keywords = [w for w, _ in keyword_counter.most_common(10)]

            categories = dict(entry["categories"])

            conv_ids = entry["conversations"]
            thread_count = len(conv_ids)
            depths = [conversation_sizes.get(cid, 0) for cid in conv_ids]
            if depths:
                avg_depth = float(statistics.mean(depths))
                max_depth = int(max(depths))
            else:
                avg_depth = 0.0
                max_depth = 0

            sig = ProfessionalSignal(
                source=self.name,
                total_emails=total,
                sent_to_you=sent_to_you,
                you_sent=you_sent,
                first_date=_unix_to_iso(entry["first_ts"]),
                last_date=_unix_to_iso(entry["last_ts"]),
                temporal_buckets=dict(entry["buckets"]),
                temporal_pattern=_compute_temporal_pattern(entry["buckets"]),
                bidirectional_ratio=float(ratio),
                subject_keywords=top_keywords,
                subject_categories=categories,
                thread_count=thread_count,
                avg_thread_depth=avg_depth,
                max_thread_depth=max_depth,
            )

            info = person_index.get(pid) or {}
            ps = PersonSignals(
                person_id=pid,
                person_name=info.get("name", ""),
                extracted_at=now_iso,
                source_coverage=[self.name],
            )
            ps.professional.append(sig)
            results[pid] = ps

        return results
