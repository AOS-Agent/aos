"""Apple Mail desktop ingest — reads ~/Library/Mail/V*/MailData/Envelope Index.

Apple Mail keeps a SQLite metadata cache (Envelope Index) at:

  ~/Library/Mail/V10/MailData/Envelope Index

It contains every message's sender, recipients, subject, date, mailbox,
thread, and flags — but NOT the body. Bodies live in per-message .emlx
files inside ``<account>/<mailbox>.mbox/Data/Messages/``. This module
ingests **metadata only** (Phase 1) into ``comms.db.messages`` with
``channel='email'``. A separate Phase 2 backfill (TBD) can walk the
.emlx tree and UPDATE rows with body content.

Phase 1 is enough to make email show up alongside iMessage / SMS /
WhatsApp in person profiles, because the profile compiler already
groups by channel + direction. Subjects make decent searchable content
on their own.

Pattern mirrors ``imessage_desktop.py``:
  - Snapshot the source DB to /tmp via macos_protected.safe_snapshot
  - Query joined message + sender + subject + recipients
  - Resolve email addresses → person_id via people.db.person_identifiers
  - Persist to comms.db.messages with deterministic IDs ``em_<rowid>``
  - Idempotent on rerun via INSERT OR IGNORE

Used by:
  - people-intel-refresh nightly cron  — incremental refresh (--days 30)
  - run directly for a full backfill    — `python3 -m
    core.engine.comms.channels.apple_mail_desktop`
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Make the repo root importable when run directly as a script or via -m.
# This file lives at core/engine/comms/channels/ — parents[4] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.util.macos_protected import (  # noqa: E402
    ensure_access,
    safe_snapshot,
)

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────


def _find_envelope_index() -> Path | None:
    """Locate the highest-numbered Envelope Index under ~/Library/Mail."""
    mail_root = Path.home() / "Library" / "Mail"
    if not mail_root.exists():
        return None
    candidates = sorted(
        (p for p in mail_root.glob("V*/MailData/Envelope Index") if p.exists()),
        key=lambda p: int(re.sub(r"\D", "", p.parts[-3]) or 0),
        reverse=True,
    )
    return candidates[0] if candidates else None


ENVELOPE_INDEX = _find_envelope_index()
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

# Mailbox URL substrings that indicate the message was SENT by the operator.
# Apple Mail uses different conventions per provider; this list covers the
# common ones (IMAP, iCloud, Gmail, Exchange).
_OUTBOUND_HINTS = (
    "/Sent Messages",
    "/Sent",
    "/Drafts",
    "[Gmail]/Sent Mail",
    "[Google Mail]/Sent Mail",
    "/Sent Items",
    "/Outbox",
)

# Senders we explicitly skip — automated, transactional, system.
_AUTOMATED_PATTERNS = re.compile(
    r"(?:^|\W)(no-?reply|do-?not-?reply|noreply|mailer-daemon|postmaster|"
    r"notifications?|alerts?|notify|info|support|hello|team|admin|"
    r"newsletter|news|updates?|unsubscribe|bounce|automated|"
    r"system|root|daemon)(?:@|\W|$)",
    re.IGNORECASE,
)


# ── Stats ────────────────────────────────────────────────────────────────


@dataclass
class IngestStats:
    total_scanned: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_automated: int = 0
    skipped_no_address: int = 0
    person_matches: int = 0
    unresolved_addresses: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"scanned:           {self.total_scanned:,}",
            f"inserted:          {self.inserted:,}",
            f"duplicates:        {self.skipped_existing:,}",
            f"skipped (auto):    {self.skipped_automated:,}",
            f"skipped (no addr): {self.skipped_no_address:,}",
            f"person matches:    {self.person_matches:,}",
            f"unresolved addrs:  {self.unresolved_addresses}",
        ]
        if self.earliest and self.latest:
            lines.append(f"date range:        {self.earliest.date()} → {self.latest.date()}")
        if self.errors:
            lines.append(f"errors:            {len(self.errors)}")
        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────────────


def is_available() -> bool:
    return ENVELOPE_INDEX is not None and ENVELOPE_INDEX.exists()


def _is_outbound_mailbox(mailbox_url: str) -> bool:
    if not mailbox_url:
        return False
    return any(hint in mailbox_url for hint in _OUTBOUND_HINTS)


def _is_automated(address: str) -> bool:
    if not address:
        return True
    return bool(_AUTOMATED_PATTERNS.search(address))


def _conv_id_for_thread(conversation_id: int) -> str:
    return f"conv_em_{conversation_id}"


def _from_unix(ts: int | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except (OSError, ValueError, OverflowError):
        return None


# ── Person resolution ───────────────────────────────────────────────────


def _build_email_person_map(
    addresses: set[str],
    people_conn: sqlite3.Connection,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not addresses:
        return mapping

    addrs = [a.strip().lower() for a in addresses if a and "@" in a]
    CHUNK = 500
    for i in range(0, len(addrs), CHUNK):
        chunk = addrs[i : i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = people_conn.execute(
            f"SELECT normalized, person_id FROM person_identifiers "
            f"WHERE type='email' AND normalized IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for normalized, person_id in rows:
            if normalized:
                mapping[normalized.lower()] = person_id
    return mapping


# ── Scan ────────────────────────────────────────────────────────────────


def scan_messages(
    since: datetime | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read all (non-deleted) email metadata from Envelope Index."""
    if not is_available():
        logger.warning("Apple Mail Envelope Index not found")
        return []

    snap = safe_snapshot(ENVELOPE_INDEX, cache_key="apple-mail-envelope")
    if snap is None:
        return []

    conn = sqlite3.connect(str(snap))
    conn.row_factory = sqlite3.Row

    try:
        # Pre-load mailbox URLs (small table)
        mailbox_urls: dict[int, str] = {}
        for r in conn.execute("SELECT ROWID, url FROM mailboxes"):
            mailbox_urls[r["ROWID"]] = r["url"] or ""

        query = """
            SELECT
                m.ROWID                AS rowid,
                m.global_message_id    AS gmid,
                m.date_received        AS date_received,
                m.date_sent            AS date_sent,
                m.subject_prefix       AS subject_prefix,
                m.mailbox              AS mailbox_id,
                m.conversation_id      AS conversation_id,
                m.flags                AS flags,
                m.deleted              AS deleted,
                m.size                 AS size,
                s.subject              AS subject,
                a.address              AS sender_address,
                a.comment              AS sender_name
            FROM messages m
            LEFT JOIN subjects s   ON m.subject = s.ROWID
            LEFT JOIN addresses a  ON m.sender  = a.ROWID
            WHERE m.deleted = 0
        """
        params: list = []
        if since:
            query += " AND m.date_received >= ?"
            params.append(int(since.timestamp()))
        query += " ORDER BY m.date_received"
        if limit:
            query += f" LIMIT {int(limit)}"

        rows = conn.execute(query, params).fetchall()

        # Pre-load recipients in one shot — much faster than per-message
        msg_ids = [r["rowid"] for r in rows]
        recipients_by_msg: dict[int, list[dict]] = {}
        if msg_ids:
            CHUNK = 500
            for i in range(0, len(msg_ids), CHUNK):
                chunk = msg_ids[i : i + CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rec_rows = conn.execute(
                    f"""
                    SELECT r.message, r.type, r.position,
                           a.address AS address, a.comment AS name
                    FROM recipients r
                    LEFT JOIN addresses a ON r.address = a.ROWID
                    WHERE r.message IN ({placeholders})
                    ORDER BY r.message, r.position
                    """,
                    tuple(chunk),
                ).fetchall()
                for r in rec_rows:
                    recipients_by_msg.setdefault(r["message"], []).append({
                        "address": (r["address"] or "").strip().lower(),
                        "name": r["name"],
                        "type": r["type"],  # 0=To, 1=Cc, 2=Bcc
                        "position": r["position"],
                    })
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        ts = _from_unix(r["date_received"]) or _from_unix(r["date_sent"])
        if ts is None:
            continue

        sender_addr = (r["sender_address"] or "").strip().lower()
        mailbox_url = mailbox_urls.get(r["mailbox_id"], "")
        is_outbound = _is_outbound_mailbox(mailbox_url)

        recipients = recipients_by_msg.get(r["rowid"], [])
        to_recipients = [rc for rc in recipients if rc["type"] == 0]
        primary_recipient = to_recipients[0]["address"] if to_recipients else ""

        # Direction & sender/recipient ids for our schema
        if is_outbound:
            direction = "outbound"
            sender_id = "me"
            recipient_id = primary_recipient
        else:
            direction = "inbound"
            sender_id = sender_addr
            recipient_id = "me"

        if not (sender_addr or primary_recipient):
            continue

        # subject + (optional) prefix
        subj = r["subject"] or ""
        if r["subject_prefix"]:
            subj = (r["subject_prefix"] + subj).strip()

        msg_id = f"em_{r['rowid']}"
        conv_id = _conv_id_for_thread(r["conversation_id"]) if r["conversation_id"] else f"conv_em_msg_{r['rowid']}"

        channel_meta = {
            "gmid": r["gmid"],
            "mailbox_url": mailbox_url,
            "subject": subj,
            "sender_address": sender_addr,
            "sender_name": r["sender_name"],
            "to": [rc["address"] for rc in to_recipients if rc["address"]],
            "cc": [rc["address"] for rc in recipients if rc["type"] == 1 and rc["address"]],
            "size": r["size"],
            "flags": r["flags"],
            "rowid": r["rowid"],
        }

        out.append(
            {
                "id": msg_id,
                "channel": "email",
                "direction": direction,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "content": subj,  # Phase 1 — subject as content; bodies in Phase 2
                "timestamp": ts.isoformat(),
                "has_attachment": 0,
                "attachment_type": None,
                "attachment_path": None,
                "channel_metadata": json.dumps(channel_meta, ensure_ascii=False),
                "conversation_id": conv_id,
                "_sender_address": sender_addr,
                "_recipients": [rc["address"] for rc in recipients if rc["address"]],
                "_to": [rc["address"] for rc in to_recipients if rc["address"]],
                "_is_outbound": is_outbound,
                "_subject": subj,
                "_ts": ts,
            }
        )

    return out


# ── Ingest ──────────────────────────────────────────────────────────────


def ingest(
    since: datetime | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    progress_cb=None,
    skip_automated: bool = True,
) -> IngestStats:
    """Read Apple Mail history and persist to comms.db."""
    stats = IngestStats()

    # Pre-flight FDA check (suppresses prompts on failure)
    if not is_available():
        stats.errors.append("Envelope Index not found")
        return stats
    if not ensure_access(ENVELOPE_INDEX, "Apple Mail Envelope Index"):
        stats.errors.append("Full Disk Access required")
        return stats

    if progress_cb:
        progress_cb("scanning", 0, 0)

    raw_messages = scan_messages(since=since, limit=limit)

    # Filter automated senders
    messages: list[dict] = []
    for m in raw_messages:
        if skip_automated and (
            _is_automated(m["_sender_address"])
            or any(_is_automated(a) for a in m["_to"])
        ):
            stats.skipped_automated += 1
            continue
        messages.append(m)

    stats.total_scanned = len(messages)
    if not messages:
        return stats

    stats.earliest = min(m["_ts"] for m in messages)
    stats.latest = max(m["_ts"] for m in messages)

    # Resolve addresses → person_id
    if progress_cb:
        progress_cb("resolving", 0, stats.total_scanned)

    addr_to_person: dict[str, str] = {}
    if PEOPLE_DB.exists():
        unique_addrs: set[str] = set()
        for m in messages:
            if m["_sender_address"]:
                unique_addrs.add(m["_sender_address"])
            for r in m["_recipients"]:
                if r:
                    unique_addrs.add(r)

        people_conn = sqlite3.connect(str(PEOPLE_DB))
        try:
            addr_to_person = _build_email_person_map(unique_addrs, people_conn)
        finally:
            people_conn.close()

        stats.unresolved_addresses = len(unique_addrs) - len(addr_to_person)

    if dry_run:
        return stats

    if not COMMS_DB.exists():
        raise FileNotFoundError(f"comms.db not found at {COMMS_DB}")

    if progress_cb:
        progress_cb("persisting", 0, stats.total_scanned)

    conn = sqlite3.connect(str(COMMS_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        # Count pre-existing for accurate insert reporting
        ids = [m["id"] for m in messages]
        existing_ids: set[str] = set()
        CHUNK = 500
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id FROM messages WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            existing_ids.update(r[0] for r in rows)
        stats.skipped_existing = len(existing_ids)

        # Build batch
        batch = []
        for m in messages:
            if m["_is_outbound"]:
                # Outbound — primary recipient is the person
                primary = m["_to"][0] if m["_to"] else ""
                person_id = addr_to_person.get(primary)
            else:
                person_id = addr_to_person.get(m["_sender_address"])

            if person_id:
                stats.person_matches += 1

            batch.append((
                m["id"],
                m["channel"],
                m["direction"],
                m["sender_id"],
                m["recipient_id"],
                m["content"],
                m["timestamp"],
                m["has_attachment"],
                m["attachment_type"],
                m["attachment_path"],
                m["channel_metadata"],
                person_id,
                m["conversation_id"],
            ))

        INSERT_SQL = """
            INSERT OR IGNORE INTO messages
              (id, channel, direction, sender_id, recipient_id,
               content, timestamp, has_attachment, attachment_type,
               attachment_path, channel_metadata, person_id, conversation_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        INSERT_CHUNK = 500
        for i in range(0, len(batch), INSERT_CHUNK):
            conn.executemany(INSERT_SQL, batch[i : i + INSERT_CHUNK])
            if progress_cb:
                progress_cb(
                    "persisting",
                    min(i + INSERT_CHUNK, len(batch)),
                    len(batch),
                )

        conn.commit()
        stats.inserted = stats.total_scanned - stats.skipped_existing
    except Exception as e:
        conn.rollback()
        stats.errors.append(str(e))
        logger.exception("Apple Mail ingest failed")
        raise
    finally:
        conn.close()

    return stats


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest Apple Mail metadata from Envelope Index into comms.db"
    )
    parser.add_argument("--days", type=int, default=None,
                        help="Only ingest messages from last N days (default: all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Hard cap on rows scanned (testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be ingested without writing")
    parser.add_argument("--keep-automated", action="store_true",
                        help="Don't filter no-reply / newsletter senders")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_available():
        print("error: Apple Mail Envelope Index not found at ~/Library/Mail/V*/MailData",
              file=sys.stderr)
        return 2

    since = None
    if args.days:
        from datetime import timedelta
        since = datetime.now() - timedelta(days=args.days)
        print(f"Cutoff: messages on or after {since.date()}")

    print(f"Source: {ENVELOPE_INDEX}")
    print("Snapshotting Envelope Index and scanning messages...")

    def _cb(phase, current, total):
        if total and current % 5000 == 0:
            print(f"  {phase}: {current:,} / {total:,}")

    stats = ingest(
        since=since,
        limit=args.limit,
        dry_run=args.dry_run,
        progress_cb=_cb,
        skip_automated=not args.keep_automated,
    )

    print()
    print(stats.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
