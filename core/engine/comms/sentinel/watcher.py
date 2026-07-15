"""Instant trigger detection via macOS kqueue.

Watches ~/Library/Messages/chat.db-wal for write events using the native
kqueue/kevent interface. When SQLite commits an outbound message, the WAL
file changes — we react within ~100ms instead of waiting up to 30 seconds
for the comms-bus poll.

Flow:
    chat.db-wal write detected (kqueue)
        ↓
    Query chat.db (immutable, read-only — no copy) for new outbound msgs
    since the last cursor we processed
        ↓
    Run trigger detection
        ↓
    Write agent_triggers row + call SentinelSpawner.handle_trigger() inline
        ↓
    Persist the new cursor

Persistence: ~/.aos/work/sentinel/.cursor (a single integer — the highest
chat.db message rowid we've processed).

Failure mode: if WAL is rotated/deleted (SQLite checkpoint), we re-open
the descriptor and resume. Never crashes the loop.
"""

from __future__ import annotations

import logging
import os
import select
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

HOME = Path.home()
CHAT_DB = HOME / "Library" / "Messages" / "chat.db"
CHAT_WAL = HOME / "Library" / "Messages" / "chat.db-wal"
CURSOR_FILE = HOME / ".aos" / "work" / "sentinel" / ".cursor"
CONFIG_PATH = HOME / ".aos" / "config" / "sentinel.yaml"

# Apple Mac Absolute Time epoch (2001-01-01)
MAC_EPOCH = 978307200
DEBOUNCE_MS = 200
WATCH_TIMEOUT_S = 60.0     # heartbeat — re-check anyway in case we miss an event


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _read_cursor() -> int:
    try:
        return int(CURSOR_FILE.read_text().strip())
    except Exception:
        return 0


def _write_cursor(rowid: int) -> None:
    try:
        CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        CURSOR_FILE.write_text(str(rowid))
    except Exception as e:
        log.warning("cursor write failed: %s", e)


def _open_chat_db_ro() -> sqlite3.Connection:
    """Open chat.db read-only without copying. Apple's WAL is fine for reads."""
    uri = f"file:{CHAT_DB}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def _resolve_recipient(chat_rowid: int) -> Optional[str]:
    """Map chat rowid to the other participant's handle."""
    try:
        conn = _open_chat_db_ro()
        row = conn.execute("""
            SELECT h.id FROM chat_handle_join chj
            JOIN handle h ON chj.handle_id = h.ROWID
            WHERE chj.chat_id = ?
            LIMIT 1
        """, (chat_rowid,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


class SentinelWatcher:
    """kqueue-based watcher on chat.db / chat.db-wal."""

    def __init__(self):
        self.cfg = _load_config()
        self._stop = threading.Event()
        self._cursor = _read_cursor()
        self._kq: Optional[select.kqueue] = None
        self._fds: list[int] = []
        # Detector built lazily so config reloads work
        self._detector = None
        # First run: jump cursor to current max so we don't backfill ancient messages
        if self._cursor == 0:
            try:
                conn = _open_chat_db_ro()
                row = conn.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM message WHERE is_from_me = 1"
                ).fetchone()
                conn.close()
                self._cursor = int(row[0])
                _write_cursor(self._cursor)
                log.info("watcher: first run — cursor jumped to current max=%d",
                         self._cursor)
            except Exception as e:
                log.warning("watcher: could not init cursor to max: %s", e)
        log.info("watcher: starting cursor=%d", self._cursor)

    @property
    def enabled(self) -> bool:
        cfg = _load_config()
        return bool(cfg.get("enabled", True)) and not bool(cfg.get("paused", False))

    @property
    def detector(self):
        if self._detector is None:
            from core.engine.comms.triggers.detector import TriggerDetector
            phrases = self.cfg.get("trigger_phrases",
                                   ["consider it done", "@aos"])
            self._detector = TriggerDetector(phrases)
        return self._detector

    # ── kqueue lifecycle ────────────────────────────────────────────

    def _register_fd(self, path: Path) -> Optional[int]:
        try:
            fd = os.open(str(path), os.O_EVTONLY)
        except FileNotFoundError:
            return None
        except Exception as e:
            log.warning("watcher: open(%s) failed: %s", path, e)
            return None

        flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND \
                | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME \
                | select.KQ_NOTE_REVOKE
        ke = select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=flags,
        )
        try:
            self._kq.control([ke], 0)
        except Exception as e:
            log.warning("watcher: kqueue register(%s) failed: %s", path, e)
            os.close(fd)
            return None
        self._fds.append(fd)
        log.info("watcher: registered fd=%d for %s", fd, path)
        return fd

    def _setup(self) -> None:
        self._kq = select.kqueue()
        for p in (CHAT_DB, CHAT_WAL):
            self._register_fd(p)

    def _teardown(self) -> None:
        for fd in self._fds:
            try:
                os.close(fd)
            except Exception:
                pass
        self._fds = []
        if self._kq is not None:
            try:
                self._kq.close()
            except Exception:
                pass
            self._kq = None

    def stop(self) -> None:
        self._stop.set()

    # ── Main loop ───────────────────────────────────────────────────

    def run(self) -> None:
        self._setup()
        log.info("watcher: running. cursor=%d", self._cursor)
        try:
            # Initial scan in case messages arrived while we were down
            self._process_new_messages("startup")

            while not self._stop.is_set():
                events = self._kq.control(None, 16, WATCH_TIMEOUT_S)
                if self._stop.is_set():
                    break
                if not events:
                    # Heartbeat — re-scan defensively
                    self._process_new_messages("heartbeat")
                    continue

                # Debounce: many writes can land back-to-back
                time.sleep(DEBOUNCE_MS / 1000.0)

                # Check if any fd was deleted/renamed (WAL rotation)
                fd_lost = any(
                    e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
                                | select.KQ_NOTE_REVOKE)
                    for e in events
                )
                if fd_lost:
                    log.info("watcher: file rotation detected — re-registering")
                    self._teardown()
                    time.sleep(0.2)
                    self._setup()

                self._process_new_messages("event")
        except Exception as e:
            log.exception("watcher loop crashed: %s", e)
        finally:
            self._teardown()
            log.info("watcher: stopped")

    # ── Detection ───────────────────────────────────────────────────

    def _process_new_messages(self, reason: str) -> int:
        """Query chat.db for outbound messages newer than cursor; detect triggers."""
        if not self.enabled:
            return 0
        rows = self._fetch_new_outbound()
        if not rows:
            return 0

        log.debug("watcher: scanning %d new outbound msg(s) (reason=%s)",
                  len(rows), reason)
        fired = 0
        max_rowid = self._cursor
        for r in rows:
            # Defensive: some sqlite3.Row → dict conversions can drop expected
            # keys on edge schemas; never crash the watcher loop on a single row.
            rowid = r.get("rowid") if isinstance(r, dict) else None
            if rowid is None:
                try:
                    rowid = r["rowid"]
                except (KeyError, IndexError, TypeError):
                    log.warning("watcher: row missing rowid (keys=%s); skipping",
                                list(r.keys()) if hasattr(r, "keys") else "?")
                    continue
            max_rowid = max(max_rowid, rowid)
            text = (r.get("text") if isinstance(r, dict) else r["text"]) or ""
            if not text.strip():
                continue
            match = self.detector.find_trigger(text)
            if not match:
                continue
            ok = self._dispatch_trigger(r, match.phrase)
            if ok:
                fired += 1
                log.info("watcher: trigger fired phrase=%s rowid=%d",
                         match.phrase, rowid)

        if max_rowid > self._cursor:
            self._cursor = max_rowid
            _write_cursor(max_rowid)
        return fired

    def _fetch_new_outbound(self) -> list[dict]:
        """Fetch new outbound messages. Decodes attributedBody when text NULL.

        Modern macOS stores text in attributedBody (NSAttributedString blob)
        rather than the plain text column. We pull both and resolve.
        """
        from .attributedbody import extract_text
        try:
            conn = _open_chat_db_ro()
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT m.rowid AS rowid, m.guid, m.text, m.attributedBody, m.date,
                       m.is_from_me,
                       c.rowid AS chat_rowid, c.chat_identifier
                FROM message m
                LEFT JOIN chat_message_join cmj ON m.rowid = cmj.message_id
                LEFT JOIN chat c ON cmj.chat_id = c.rowid
                WHERE m.rowid > ? AND m.is_from_me = 1
                ORDER BY m.rowid ASC
                LIMIT 50
            """, (self._cursor,)).fetchall()
            conn.close()
            # Resolve text (column or attributedBody) for each row
            out: list[dict] = []
            for r in rows:
                d = {k: r[k] for k in r.keys()}
                text = d.get("text")
                if not text:
                    text = extract_text(d.get("attributedBody"))
                d["text"] = text or ""
                out.append(d)
            return out
        except Exception as e:
            log.warning("watcher: query failed: %s", e)
            return []

    def _dispatch_trigger(self, row: sqlite3.Row, phrase: str) -> bool:
        """Write agent_triggers row + invoke spawner inline."""
        import sqlite3 as _sql
        import uuid
        from pathlib import Path as _Path

        COMMS_DB = _Path.home() / ".aos" / "data" / "comms.db"
        rowid = row["rowid"]
        # Synthesise the comms.db-style message id used elsewhere
        msg_id = f"im-{rowid}"
        chat_rowid = row["chat_rowid"]
        channel = "imessage"
        trigger_id = f"trg_{uuid.uuid4().hex[:12]}"
        now = int(time.time())

        # Insert trigger row (UNIQUE on message_id provides idempotency)
        try:
            conn = _sql.connect(str(COMMS_DB), timeout=5,
                                 isolation_level=None)
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                conn.execute("""
                    INSERT INTO agent_triggers (
                        id, message_id, person_id, channel, trigger_phrase,
                        agent_name, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (trigger_id, msg_id, None, channel, phrase,
                      "sentinel", "detected", now))
            except _sql.IntegrityError:
                conn.close()
                log.debug("watcher: trigger for %s already recorded", msg_id)
                return False
            conn.close()
        except Exception as e:
            log.error("watcher: trigger insert failed: %s", e)
            return False

        # Fire the ack (notification only by default — non-blocking enough)
        try:
            from .ack import send_ack
            # Build a quick task hint for the notification
            send_ack(channel, str(chat_rowid),
                     task_hint=(row["text"] or "")[:80])
        except Exception as e:
            log.warning("watcher: ack failed: %s", e)

        # Hand off to spawner in a thread so the watcher loop keeps watching
        try:
            t = threading.Thread(
                target=_run_handler, args=(trigger_id,),
                daemon=True, name=f"hay-{trigger_id[:8]}",
            )
            t.start()
        except Exception as e:
            log.exception("watcher: spawn-thread failed: %s", e)
            return False
        return True


def _run_handler(trigger_id: str) -> None:
    """Top-level thread target — invokes spawner.handle_trigger by id."""
    try:
        from .spawner import SentinelSpawner
        s = SentinelSpawner()
        # Look up the trigger row + call _handle directly
        s.handle_by_id(trigger_id)
    except Exception as e:
        log.exception("watcher: handler thread crashed: %s", e)
