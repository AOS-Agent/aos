"""iMessage ConverseChannel (PLAN.md §3).

Read-only URI open of chat.db (same approach as sentinel/watcher.py — no
copy, WAL-friendly) plus the proven AppleScript send path (lifted from
envoy/runner.py's `send_imessage`, which is itself "the existing comms
iMessage adapter" script per core/engine/comms/channels/imessage.py's
`send_message` — all three copies of this AppleScript are now identical;
this is the third, converse now shares the exact same recipe rather than
inventing a fourth).

`watch_spec()` advertises `kind='kqueue'` on chat.db/chat.db-wal; the actual
kevent loop lives in converse/kqueue_watch.py (a generic extraction of
sentinel/watcher.py's kqueue core — see that module's docstring) and is
driven by the supervisor (T3, a later wave), not by this module.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .channels import (
    InboundMsg,
    SendResult,
    WatchSpec,
    ensure_repo_root_on_path,
    resolve_contact,
)

log = logging.getLogger(__name__)

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
CHAT_WAL = Path.home() / "Library" / "Messages" / "chat.db-wal"

# Apple's Mac Absolute Time epoch (2001-01-01), matching sentinel/watcher.py.
MAC_EPOCH = 978307200


def _open_ro() -> sqlite3.Connection:
    """Read-only URI open of chat.db — no copy, matches
    sentinel/watcher.py's `_open_chat_db_ro`. Requires Full Disk Access."""
    uri = f"file:{CHAT_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_text(row: sqlite3.Row) -> str:
    text = row["text"]
    if text:
        return text
    ensure_repo_root_on_path()
    from core.engine.comms.sentinel.attributedbody import extract_text
    return extract_text(row["attributedBody"]) or ""


def _date_to_iso(date_val: int | None) -> str:
    """message.date is nanoseconds since MAC_EPOCH on modern macOS (older
    rows may be plain seconds, but converse only reads live/recent traffic
    so nanosecond-scale is the only case that matters in practice)."""
    if not date_val:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    seconds = MAC_EPOCH + (date_val / 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")


class iMessageChannel:
    """Implements converse.channels.ConverseChannel for iMessage."""

    name = "imessage"

    def poll(
        self, conversation_ref: str, counterpart_handle: str, cursor: str | None
    ) -> tuple[list[InboundMsg], str | None]:
        after = int(cursor) if cursor else 0
        try:
            conn = _open_ro()
        except Exception as e:
            # Full Disk Access missing/DB locked/etc — a health problem, not
            # an auth problem (needs_reauth() is always False for iMessage
            # per PLAN.md §3); log and report "nothing new" so the
            # supervisor just retries next tick rather than crashing a pass.
            log.warning("imessage poll: could not open chat.db: %s", e)
            return [], None

        try:
            rows = conn.execute(
                """
                SELECT m.rowid AS rowid, m.guid, m.text, m.attributedBody, m.date
                FROM message m
                JOIN chat_message_join cmj ON m.rowid = cmj.message_id
                JOIN chat c ON cmj.chat_id = c.rowid
                WHERE c.chat_identifier = ? AND m.is_from_me = 0 AND m.rowid > ?
                ORDER BY m.rowid ASC
                LIMIT 200
                """,
                (conversation_ref, after),
            ).fetchall()
        except Exception as e:
            log.warning("imessage poll: query failed for %s: %s", conversation_ref, e)
            return [], None
        finally:
            conn.close()

        if not rows:
            return [], None

        inbound = [
            InboundMsg(
                channel_message_id=row["guid"],
                text=_extract_text(row),
                ts=_date_to_iso(row["date"]),
            )
            for row in rows
        ]
        new_cursor = str(rows[-1]["rowid"])
        return inbound, new_cursor

    def send(self, conversation_ref: str, text: str) -> SendResult:
        """AppleScript send — conversation_ref is chat.db's chat_identifier,
        which for 1:1 iMessage IS the recipient handle (phone/email), so it
        is used directly as the `participant` target."""
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        safe_rcpt = conversation_ref.replace("\\", "\\\\").replace('"', '\\"')
        script = f'''
            tell application "Messages"
                set targetService to 1st account whose service type = iMessage
                set targetBuddy to participant "{safe_rcpt}" of targetService
                send "{safe_text}" to targetBuddy
            end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=20
            )
        except subprocess.TimeoutExpired as e:
            return SendResult(ok=False, error=f"timeout: {e}")
        except Exception as e:
            return SendResult(ok=False, error=str(e))

        if result.returncode != 0:
            return SendResult(ok=False, error=(result.stderr or "").strip()[:300])
        return SendResult(ok=True)

    def resolve_counterpart(self, handle: str) -> dict | None:
        result = resolve_contact(handle)
        if not result.get("resolved"):
            return None
        contact = result.get("contact") or {}
        return {
            "person_id": result.get("person_id"),
            "canonical_name": contact.get("canonical_name"),
            "importance": contact.get("importance"),
        }

    def needs_reauth(self) -> bool:
        # Full Disk Access is a health check, not an auth loop (PLAN.md §3).
        return False

    def watch_spec(self) -> WatchSpec:
        return WatchSpec(kind="kqueue", paths=[CHAT_DB, CHAT_WAL])
