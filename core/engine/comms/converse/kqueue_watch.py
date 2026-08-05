"""Generic kqueue-based file watcher — extracted from sentinel/watcher.py's
kevent/rotation core (PLAN.md §3 iMessage watch_spec, §8 Phase D).

`~/aos/core/engine/comms/sentinel/watcher.py` (today's live iMessage trigger
watcher) has its own private copy of this exact kevent-register / debounce /
WAL-rotation-recovery logic, hardwired to chat.db/chat.db-wal and its own
trigger-detection callback. This module is the SAME algorithm, generalized:
any list of paths, any on-wake callback.

IMPORTANT — this build (T2a) does NOT modify or import sentinel/watcher.py,
and sentinel/watcher.py is NOT touched by this change at all: it keeps
running exactly as it does today, unaware this module exists. PLAN.md §8
Phase D (a later, separate, sequential task — "Sentinel onto the runtime")
is where sentinel's watcher is switched to import this shared class instead
of keeping its private copy ("one implementation, no fork"); that is an
explicit deviation-with-justification from a literal same-day
extract-with-import-shim: touching a live, unrelated-team watcher inside a
parallel Wave-1 channel-adapter task carried real risk of breaking Sentinel
for a plan section (Phase D) that is sequenced after Wave 2/3 anyway. Until
Phase D there are knowingly two copies of ~80 lines of kqueue plumbing; see
PLAN.md §8 for the migration that unifies them.

Used by the converse supervisor (T3, not part of T2a) to drive
ConverseChannel implementations whose watch_spec() reports kind='kqueue'
(currently: iMessage — see converse/channels_imessage.py).
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

DEBOUNCE_MS = 200
DEFAULT_HEARTBEAT_S = 60.0

# reason: "startup" | "heartbeat" | "event"
OnWake = Callable[[str], None]


class KqueueFileWatcher:
    """Watches a set of files for writes via macOS kqueue/kevent and invokes
    `on_wake(reason)`:
      - once immediately on run() (reason="startup") — catches anything that
        arrived while the watcher was down;
      - on every debounced wake event (reason="event");
      - on a heartbeat timeout with no events (reason="heartbeat") — a
        defensive re-scan in case a kevent was ever missed.

    Also owns WAL-rotation recovery: a DELETE/RENAME/REVOKE event on a
    watched fd (SQLite checkpointing chat.db-wal, for example) triggers a
    teardown + re-register instead of silently going deaf — the exact
    behavior sentinel/watcher.py relies on for chat.db-wal today.

    Callers own all query/retry logic inside on_wake(); this class only
    owns the OS-level wake-up plumbing. A per-call exception in on_wake is
    caught and logged — it never kills the watch loop.
    """

    def __init__(
        self,
        paths: list[Path],
        on_wake: OnWake,
        *,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        debounce_ms: int = DEBOUNCE_MS,
    ):
        self.paths = paths
        self.on_wake = on_wake
        self.heartbeat_s = heartbeat_s
        self.debounce_ms = debounce_ms
        self._stop = threading.Event()
        self._kq: Optional["select.kqueue"] = None
        self._fds: list[int] = []

    # ── kqueue lifecycle (extracted verbatim from sentinel/watcher.py) ──

    def _register_fd(self, path: Path) -> Optional[int]:
        try:
            fd = os.open(str(path), os.O_EVTONLY)
        except FileNotFoundError:
            return None
        except Exception as e:
            log.warning("kqueue_watch: open(%s) failed: %s", path, e)
            return None

        flags = (
            select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND
            | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
            | select.KQ_NOTE_REVOKE
        )
        ke = select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=flags,
        )
        try:
            self._kq.control([ke], 0)
        except Exception as e:
            log.warning("kqueue_watch: register(%s) failed: %s", path, e)
            os.close(fd)
            return None
        self._fds.append(fd)
        log.info("kqueue_watch: registered fd=%d for %s", fd, path)
        return fd

    def _setup(self) -> None:
        self._kq = select.kqueue()
        for p in self.paths:
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

    # ── Main loop ────────────────────────────────────────────────────

    def run(self) -> None:
        self._setup()
        log.info("kqueue_watch: running on %s", [str(p) for p in self.paths])
        try:
            self._safe_wake("startup")

            while not self._stop.is_set():
                events = self._kq.control(None, 16, self.heartbeat_s)
                if self._stop.is_set():
                    break
                if not events:
                    self._safe_wake("heartbeat")
                    continue

                # Debounce: many writes can land back-to-back.
                time.sleep(self.debounce_ms / 1000.0)

                fd_lost = any(
                    e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME | select.KQ_NOTE_REVOKE)
                    for e in events
                )
                if fd_lost:
                    log.info("kqueue_watch: file rotation detected — re-registering")
                    self._teardown()
                    time.sleep(0.2)
                    self._setup()

                self._safe_wake("event")
        except Exception:
            log.exception("kqueue_watch: loop crashed")
        finally:
            self._teardown()
            log.info("kqueue_watch: stopped")

    def _safe_wake(self, reason: str) -> None:
        try:
            self.on_wake(reason)
        except Exception:
            log.exception("kqueue_watch: on_wake(%s) crashed", reason)
