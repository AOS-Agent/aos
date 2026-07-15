"""Poll-liveness heartbeat for the Telegram channel.

The bridge's Telegram poll loop can wedge silently: the process stays alive,
launchd's KeepAlive sees a healthy PID, but python-telegram-bot's internal
getUpdates loop has stopped fetching — updates pile up on Telegram's side and,
once past the queue limit, are consumed and dropped. This happened live for
32h; the process watchdog never noticed because it only checks liveness.

This module records a timestamp every time getUpdates *successfully returns*
(including empty polls), so a stalled loop shows up as a stale timestamp. The
signal is exposed two ways:

  - a small JSON state file at ~/.aos/services/bridge/.last_poll.json, read by
    the bridge_poll_liveness reconcile check (a separate process); and
  - in-process accessors surfaced on the :4098 /health endpoint.

install_poll_heartbeat() wraps ExtBot.get_updates at the class level. Instance
assignment is blocked by PTB's __slots__, and the class-level wrap has the
bonus of leaving the ApplicationBuilder's request tuning fully intact — we do
not touch how the bot or its connection pools are built.
"""

import functools
import json
import os
import time
from pathlib import Path

_RUNTIME_DIR = Path.home() / ".aos" / "services" / "bridge"
_STATE_FILE = _RUNTIME_DIR / ".last_poll.json"

# In-process last-successful-poll time (monotonic wall clock). Always current.
_last_poll: float = 0.0

# Throttle file writes: getUpdates can return many times per second during a
# burst of updates, but the reconcile check only needs ~minute granularity.
_FILE_WRITE_INTERVAL_S = 5.0
_last_file_write: float = 0.0

# Guard so install_poll_heartbeat() is idempotent.
_orig_get_updates = None


def record_poll() -> None:
    """Mark a successful poll cycle. Cheap; safe to call on every getUpdates."""
    global _last_poll, _last_file_write
    now = time.time()
    _last_poll = now
    if now - _last_file_write < _FILE_WRITE_INTERVAL_S:
        return
    _last_file_write = now
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": now}))
        os.replace(tmp, _STATE_FILE)
    except Exception:
        # Never let a heartbeat write failure disturb the poll loop.
        pass


def last_poll_ts() -> float:
    """Last successful poll time (epoch seconds), 0.0 if none yet in-process."""
    return _last_poll


def last_poll_age() -> float | None:
    """Seconds since the last successful poll, or None if none recorded."""
    if _last_poll <= 0:
        return None
    return time.time() - _last_poll


def install_poll_heartbeat() -> None:
    """Wrap ExtBot.get_updates (class-level) to record each successful poll.

    Idempotent. Seeds an initial timestamp so a freshly started bridge is not
    momentarily seen as stale before its first poll returns.
    """
    global _orig_get_updates
    if _orig_get_updates is not None:
        return

    from telegram.ext import ExtBot

    _orig_get_updates = ExtBot.get_updates

    @functools.wraps(_orig_get_updates)
    async def _wrapped(self, *args, **kwargs):
        result = await _orig_get_updates(self, *args, **kwargs)
        record_poll()
        return result

    ExtBot.get_updates = _wrapped
    record_poll()  # seed
