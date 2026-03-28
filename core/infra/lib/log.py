"""Structured logging for AOS.

Provides a consistent JSON log format across all services, crons, and hooks.
Every component should use get_logger() instead of configuring logging manually.

Usage:
    from lib.log import get_logger
    logger = get_logger("bridge")
    logger.info("Message received", extra={"user": "hisham", "channel": "telegram"})

Output (JSONL):
    {"ts":"2026-03-27T14:30:00","level":"INFO","source":"bridge","msg":"Message received","user":"hisham","channel":"telegram"}
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def __init__(self, source: str = "aos"):
        super().__init__()
        self.source = source

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "source": self.source,
            "msg": record.getMessage(),
        }

        # Include extra fields (passed via logger.info("msg", extra={...}))
        for key, val in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "filename", "module", "pathname", "thread", "threadName",
                "process", "processName", "levelname", "levelno",
                "msecs", "message", "taskName",
            ):
                entry[key] = val

        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
            entry["error_type"] = type(record.exc_info[1]).__name__

        return json.dumps(entry, ensure_ascii=False, default=str)


def get_logger(
    name: str,
    level: str | None = None,
    log_file: str | None = None,
    max_bytes: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 3,
) -> logging.Logger:
    """Create a consistently configured logger.

    Args:
        name: Logger/source name (e.g., "bridge", "watchdog", "engine").
        level: Log level override. Default: INFO, or AOS_LOG_LEVEL env var.
        log_file: Path to log file. If set, adds a rotating file handler.
        max_bytes: Max log file size before rotation (default: 5MB).
        backup_count: Number of rotated files to keep (default: 3).

    Returns:
        Configured logging.Logger with JSON formatting.
    """
    logger = logging.getLogger(f"aos.{name}")

    # Don't re-add handlers if already configured
    if logger.handlers:
        return logger

    log_level = level or os.environ.get("AOS_LOG_LEVEL", "INFO")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    formatter = JSONFormatter(source=name)

    # Always add stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    # Optionally add rotating file handler
    if log_file:
        from logging.handlers import RotatingFileHandler

        log_path = os.path.expanduser(log_file)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Don't propagate to root logger (prevents duplicate output)
    logger.propagate = False

    return logger
