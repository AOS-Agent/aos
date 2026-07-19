"""iMessage / SMS desktop ingest — reads ~/Library/Messages/chat.db directly.

On macOS, the Messages app maintains chat.db as a plaintext SQLite
database. This module reads it directly to ingest full message history
into comms.db. Mirrors the architecture of `whatsapp_local.py`:

  - Snapshot the source DB to /tmp to avoid lock contention
  - Read messages with their handles + chats joined
  - Resolve handles (phones/emails) to person_id via people.db
  - Persist to comms.db.messages with deterministic IDs ``im_<rowid>``
  - Idempotent: re-runs are safe (INSERT OR IGNORE on stable IDs)

Used by:
  - people-intel-refresh nightly cron  — incremental refresh (--days 7)
  - run directly for a full backfill    — `python3 -m
    core.engine.comms.channels.imessage_desktop`

Data source:
  ~/Library/Messages/chat.db
  Requires Full Disk Access for the running process.

Schema notes (message):
  ROWID         stable PK — used for deterministic message IDs
  guid          GUID (we keep it in channel_metadata for cross-ref)
  text          message body (NULL for some — see attributedBody)
  date          Mac timestamp (nanoseconds since 2001-01-01 on iOS 13+;
                seconds for older rows)
  is_from_me    1 = outbound, 0 = inbound
  service       'iMessage', 'SMS', 'RCS'
  handle_id     FK to handle.ROWID (the OTHER party in 1:1 chats)
  cache_roomnames  group chat identifier when present (legacy)

Schema notes (handle):
  ROWID, id (phone/email string), service

Schema notes (chat):
  ROWID, guid, chat_identifier, display_name, room_name, style
  style 45 = 1:1 chat, style 43 = group
"""
from __future__ import annotations

import hashlib
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

CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

# Apple epoch offset: seconds between 1970-01-01 and 2001-01-01
APPLE_EPOCH = 978307200
NANOSECOND = 1_000_000_000

# Service → comms.db channel name
_SERVICE_CHANNEL = {
    "iMessage": "imessage",
    "SMS": "sms",
    "RCS": "rcs",
}


# ── Stats container ──────────────────────────────────────────────────────


@dataclass
class IngestStats:
    total_scanned: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_no_text: int = 0
    conversations_created: int = 0
    conversations_updated: int = 0
    person_matches: int = 0
    unresolved_handles: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"scanned:           {self.total_scanned:,}",
            f"inserted:          {self.inserted:,}",
            f"duplicates:        {self.skipped_existing:,}",
            f"skipped (no text): {self.skipped_no_text:,}",
            f"conversations (new/updated): {self.conversations_created} / {self.conversations_updated}",
            f"person matches:    {self.person_matches:,}",
            f"unresolved handles: {self.unresolved_handles}",
        ]
        if self.earliest and self.latest:
            lines.append(f"date range:        {self.earliest.date()} → {self.latest.date()}")
        if self.errors:
            lines.append(f"errors:            {len(self.errors)}")
        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────────────


def is_available() -> bool:
    return CHAT_DB_PATH.exists()


def _from_mac_ts(ts: int | None) -> datetime | None:
    """Convert chat.db `date` to a naive datetime.

    chat.db uses nanoseconds since 2001-01-01 on iOS 13+ (~10^18) and
    seconds since 2001-01-01 on older rows (~10^9). Heuristic: anything
    over 10^15 is nanoseconds.
    """
    if not ts:
        return None
    try:
        if ts > 10**15:
            seconds = ts / NANOSECOND
        else:
            seconds = float(ts)
        return datetime.fromtimestamp(seconds + APPLE_EPOCH)
    except (OSError, OverflowError, ValueError):
        return None


def _to_mac_ts(dt: datetime) -> int:
    """Inverse of _from_mac_ts — produces nanosecond format."""
    seconds = dt.timestamp() - APPLE_EPOCH
    return int(seconds * NANOSECOND)


def _normalize_phone_handle(handle: str) -> str | None:
    """Normalize a phone handle to E.164 (+digits).

    iMessage handles look like '+12025550143' (E.164) or '2025550143'
    (digits) or 'user@example.com' (email). Returns +digits for phones,
    None for emails.
    """
    if not handle or "@" in handle:
        return None
    digits = re.sub(r"[^\d]", "", handle)
    if len(digits) < 7:
        return None
    return "+" + digits


def _normalize_email_handle(handle: str) -> str | None:
    if not handle or "@" not in handle:
        return None
    return handle.strip().lower()


def _conv_id_for_chat(chat_guid: str) -> str:
    """Deterministic conversation ID from a chat guid."""
    h = hashlib.sha1((chat_guid or "").encode()).hexdigest()[:12]
    return f"conv_im_{h}"


def _snapshot_chat_db() -> Path | None:
    """Snapshot chat.db via the shared FDA-aware cache helper.

    Returns None if Full Disk Access is missing — caller should bail
    cleanly without retrying. The snapshot is cached in /tmp for the
    default TTL so back-to-back ingests don't re-trigger TCC.
    """
    return safe_snapshot(CHAT_DB_PATH, cache_key="imessage-chat-db")


# ── Person resolution ────────────────────────────────────────────────────


def _build_handle_person_map(
    handles: set[str],
    people_conn: sqlite3.Connection,
) -> dict[str, str]:
    """Resolve iMessage handles (phone/email) to person_id via people.db.

    Two passes:
      1. Phone handles → normalize to +digits → person_identifiers.normalized
         (type='phone')
      2. Email handles → lowercase → person_identifiers.normalized
         (type='email')
    """
    mapping: dict[str, str] = {}
    if not handles:
        return mapping

    phone_to_handle: dict[str, str] = {}
    email_to_handle: dict[str, str] = {}
    for h in handles:
        phone = _normalize_phone_handle(h)
        if phone:
            phone_to_handle.setdefault(phone, h)
            continue
        email = _normalize_email_handle(h)
        if email:
            email_to_handle.setdefault(email, h)

    CHUNK = 500

    if phone_to_handle:
        phones = list(phone_to_handle.keys())
        for i in range(0, len(phones), CHUNK):
            chunk = phones[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = people_conn.execute(
                f"SELECT normalized, person_id FROM person_identifiers "
                f"WHERE type='phone' AND normalized IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for normalized, person_id in rows:
                handle = phone_to_handle.get(normalized)
                if handle:
                    mapping[handle] = person_id

    if email_to_handle:
        emails = list(email_to_handle.keys())
        for i in range(0, len(emails), CHUNK):
            chunk = emails[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = people_conn.execute(
                f"SELECT normalized, person_id FROM person_identifiers "
                f"WHERE type='email' AND normalized IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for normalized, person_id in rows:
                handle = email_to_handle.get(normalized)
                if handle:
                    mapping[handle] = person_id

    return mapping


# ── Scan ─────────────────────────────────────────────────────────────────


def scan_messages(
    since: datetime | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read messages from chat.db ready for comms.db insertion.

    Returns dicts with both persistable columns and internal `_` fields
    used for conversation aggregation and person resolution.

    Skips rows with NULL/empty `text` (attributedBody-only messages —
    those need a typedstream decoder; tracked in stats.skipped_no_text).
    """
    if not is_available():
        logger.warning("chat.db not found at %s", CHAT_DB_PATH)
        return []

    snap = _snapshot_chat_db()
    if snap is None:
        return []  # FDA missing — instructions already printed

    try:
        conn = sqlite3.connect(str(snap))
        conn.row_factory = sqlite3.Row

        query = """
            SELECT
                m.ROWID                AS rowid,
                m.guid                 AS guid,
                m.text                 AS text,
                m.date                 AS date,
                m.is_from_me           AS is_from_me,
                m.service              AS service,
                m.cache_has_attachments AS has_attachment,
                m.item_type            AS item_type,
                m.is_system_message    AS is_system,
                m.is_emote             AS is_emote,
                h.id                   AS handle_str,
                c.guid                 AS chat_guid,
                c.chat_identifier      AS chat_identifier,
                c.display_name         AS chat_display_name,
                c.room_name            AS chat_room_name,
                c.style                AS chat_style
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN chat c ON c.ROWID = cmj.chat_id
            WHERE m.is_system_message = 0
              AND m.item_type = 0
              AND (m.text IS NOT NULL AND m.text != '')
        """
        params: list = []
        if since:
            query += " AND m.date >= ?"
            params.append(_to_mac_ts(since))
        query += " ORDER BY m.date"
        if limit:
            query += f" LIMIT {int(limit)}"

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
        # Snapshot is cached by safe_snapshot — don't delete it

    result: list[dict] = []
    for r in rows:
        ts = _from_mac_ts(r["date"])
        if ts is None:
            continue

        service = r["service"] or "iMessage"
        channel = _SERVICE_CHANNEL.get(service, "imessage")

        from_me = bool(r["is_from_me"])
        direction = "outbound" if from_me else "inbound"

        is_group = (r["chat_style"] == 43) or bool(r["chat_room_name"])

        # The "other party" handle for 1:1 chats
        other_handle = r["handle_str"] or ""
        chat_guid = r["chat_guid"] or ""
        chat_identifier = r["chat_identifier"] or other_handle

        if from_me:
            sender_id = "me"
            recipient_id = chat_identifier  # 1:1: their handle; group: chat id
        else:
            sender_id = other_handle or chat_identifier
            recipient_id = "me"

        msg_id = f"im_{r['rowid']}"
        conv_id = _conv_id_for_chat(chat_guid) if chat_guid else _conv_id_for_chat(chat_identifier)

        channel_meta = {
            "guid": r["guid"],
            "service": service,
            "chat_guid": chat_guid,
            "chat_identifier": chat_identifier,
            "is_group": is_group,
            "handle": other_handle,
            "rowid": r["rowid"],
        }
        if r["chat_display_name"]:
            channel_meta["display_name"] = r["chat_display_name"]
        if r["chat_room_name"]:
            channel_meta["room_name"] = r["chat_room_name"]

        result.append(
            {
                "id": msg_id,
                "channel": channel,
                "direction": direction,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "content": r["text"],
                "timestamp": ts.isoformat(),
                "has_attachment": 1 if r["has_attachment"] else 0,
                "attachment_type": None,
                "attachment_path": None,
                "channel_metadata": json.dumps(channel_meta),
                "conversation_id": conv_id,
                # Internal fields used by ingest()
                "_chat_guid": chat_guid,
                "_chat_identifier": chat_identifier,
                "_other_handle": other_handle,
                "_is_group": is_group,
                "_chat_display_name": r["chat_display_name"],
                "_ts": ts,
            }
        )

    return result


# ── Conversation aggregation ─────────────────────────────────────────────


def _build_conversations(messages: list[dict]) -> dict[str, dict]:
    convs: dict[str, dict] = {}
    for m in messages:
        cid = m["conversation_id"]
        identifier = m["_chat_identifier"]
        if cid not in convs:
            name = m.get("_chat_display_name") or identifier or "iMessage chat"
            convs[cid] = {
                "id": cid,
                "channel": m["channel"],
                "name": name,
                "chat_identifier": identifier,
                "is_group": m["_is_group"],
                "last_message_at": m["_ts"],
                "message_count": 0,
            }
        c = convs[cid]
        c["message_count"] += 1
        if m["_ts"] > c["last_message_at"]:
            c["last_message_at"] = m["_ts"]
    return convs


# ── Ingest ───────────────────────────────────────────────────────────────


def ingest(
    since: datetime | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    progress_cb=None,
) -> IngestStats:
    """Read iMessage history and persist to comms.db.

    Args:
        since: Only ingest messages on/after this datetime.
        limit: Hard cap on rows scanned (for testing).
        dry_run: Compute stats without writing.
        progress_cb: Optional ``cb(phase, current, total)`` for UI updates.
    """
    stats = IngestStats()

    if progress_cb:
        progress_cb("scanning", 0, 0)

    messages = scan_messages(since=since, limit=limit)
    stats.total_scanned = len(messages)

    if not messages:
        return stats

    stats.earliest = min(m["_ts"] for m in messages)
    stats.latest = max(m["_ts"] for m in messages)

    # ── Phase 2: resolve handles against people.db ───────────────────────
    if progress_cb:
        progress_cb("resolving", 0, stats.total_scanned)

    handle_to_person: dict[str, str] = {}
    if PEOPLE_DB.exists():
        unique_handles: set[str] = set()
        for m in messages:
            h = m["_other_handle"]
            if h:
                unique_handles.add(h)

        people_conn = sqlite3.connect(str(PEOPLE_DB))
        try:
            handle_to_person = _build_handle_person_map(unique_handles, people_conn)
        finally:
            people_conn.close()

        stats.unresolved_handles = len(unique_handles) - len(handle_to_person)

    # ── Phase 3: aggregate conversations ─────────────────────────────────
    conversations = _build_conversations(messages)

    # Attach person_id to 1:1 conversations
    for c in conversations.values():
        if not c["is_group"]:
            pid = handle_to_person.get(c["chat_identifier"])
            if pid:
                c["person_id"] = pid

    stats.person_matches = sum(
        1
        for m in messages
        if handle_to_person.get(m["_other_handle"])
    )

    if dry_run:
        logger.info(
            "Dry run: would persist %d messages across %d conversations",
            stats.total_scanned,
            len(conversations),
        )
        return stats

    # ── Phase 4: persist ─────────────────────────────────────────────────
    if not COMMS_DB.exists():
        raise FileNotFoundError(f"comms.db not found at {COMMS_DB}")

    if progress_cb:
        progress_cb("persisting", 0, stats.total_scanned)

    conn = sqlite3.connect(str(COMMS_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        # Upsert conversations
        for c in conversations.values():
            existing = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (c["id"],)
            ).fetchone()
            person_id = c.get("person_id")
            metadata = json.dumps(
                {"chat_identifier": c["chat_identifier"], "is_group": c["is_group"]}
            )
            last_msg_iso = c["last_message_at"].isoformat()

            if existing:
                conn.execute(
                    """UPDATE conversations SET
                         name = COALESCE(NULLIF(?, ''), name),
                         last_message_at = MAX(COALESCE(last_message_at, ''), ?),
                         message_count = ?,
                         metadata = ?,
                         person_id = COALESCE(person_id, ?)
                       WHERE id = ?""",
                    (
                        c["name"] or "",
                        last_msg_iso,
                        c["message_count"],
                        metadata,
                        person_id,
                        c["id"],
                    ),
                )
                stats.conversations_updated += 1
            else:
                conn.execute(
                    """INSERT INTO conversations
                       (id, channel, person_id, name, status, last_message_at,
                        message_count, unread_count, metadata)
                       VALUES (?, ?, ?, ?, 'open', ?, ?, 0, ?)""",
                    (
                        c["id"],
                        c["channel"],
                        person_id,
                        c["name"],
                        last_msg_iso,
                        c["message_count"],
                        metadata,
                    ),
                )
                stats.conversations_created += 1

        # Count pre-existing messages so we report accurate insert counts
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
            if m["_other_handle"] and m["_other_handle"] in handle_to_person:
                person_id = handle_to_person[m["_other_handle"]]
            elif not m["_is_group"]:
                # 1:1 chat — try the chat_identifier as the handle
                person_id = handle_to_person.get(m["_chat_identifier"])
            else:
                person_id = None

            batch.append(
                (
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
                )
            )

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
        logger.exception("iMessage ingest failed")
        raise
    finally:
        conn.close()

    return stats


# ── CLI entry point ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest iMessage / SMS history from chat.db into comms.db"
    )
    parser.add_argument("--days", type=int, default=None,
                        help="Only ingest messages from last N days (default: all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Hard cap on rows scanned (testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be ingested without writing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_available():
        print(f"error: chat.db not found at {CHAT_DB_PATH}", file=sys.stderr)
        return 2
    if not ensure_access(CHAT_DB_PATH, "iMessage chat.db"):
        return 2

    since = None
    if args.days:
        from datetime import timedelta
        since = datetime.now() - timedelta(days=args.days)
        print(f"Cutoff: messages on or after {since.date()}")

    print("Snapshotting chat.db and scanning messages...")

    def _cb(phase, current, total):
        if total and current % 5000 == 0:
            print(f"  {phase}: {current:,} / {total:,}")

    stats = ingest(since=since, limit=args.limit, dry_run=args.dry_run, progress_cb=_cb)

    print()
    print(stats.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
