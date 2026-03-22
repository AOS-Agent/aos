"""Structured event logging for the bridge.

Every significant bridge event (session start, HTML fallback, rate limit, etc.)
gets a JSON line in bridge-events.jsonl. Human logs stay in bridge.log for
readability; this file is for programmatic queries and pattern detection.

Usage:
    from bridge_events import bridge_event
    bridge_event("session_resumed", session_id="abc123", user_key="dm")
    bridge_event("html_fallback", level="warn", error="unclosed <pre>", chunk_len=2400)
"""

import json
import logging
import logging.handlers
import time
from pathlib import Path

_LOG_DIR = Path.home() / ".aos" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_event_logger = logging.getLogger("bridge.events")
_event_logger.setLevel(logging.DEBUG)
_event_logger.propagate = False  # don't duplicate into main bridge log

_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "bridge-events.jsonl",
    maxBytes=2 * 1024 * 1024,  # 2MB
    backupCount=2,
)
_handler.setFormatter(logging.Formatter("%(message)s"))
_event_logger.addHandler(_handler)

# Valid levels
_LEVELS = {"debug", "info", "warn", "error"}


def bridge_event(event: str, level: str = "info", **detail):
    """Log a structured bridge event.

    Args:
        event: Event name (e.g., "session_resumed", "html_fallback", "rate_limit")
        level: One of "debug", "info", "warn", "error"
        **detail: Arbitrary key-value pairs for event context
    """
    level = level if level in _LEVELS else "info"
    record = {
        "ts": time.time(),
        "event": event,
        "level": level,
        **detail,
    }

    log_fn = {
        "debug": _event_logger.debug,
        "info": _event_logger.info,
        "warn": _event_logger.warning,
        "error": _event_logger.error,
    }[level]

    log_fn(json.dumps(record, default=str))
