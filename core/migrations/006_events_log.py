"""
Migration 006: Initialize the event bus (events.jsonl).

A simple append-only event log that scripts can emit to and read from.
This is the coordination layer — scripts become a pipeline, not isolated crons.

Events are JSON lines: {"ts": "...", "event": "...", "source": "...", "data": {...}}

The event emitter lives at core/work/events.py (not core/lib/).
Originally this migration also created core/lib/events.py, but that was
never imported by anything — removed in cleanup (2026-03-22).
"""

DESCRIPTION = "Initialize event bus (~/.aos/events.jsonl)"

from pathlib import Path

USER_DIR = Path.home() / ".aos"
EVENTS_FILE = USER_DIR / "events.jsonl"


def check() -> bool:
    """Applied if events.jsonl exists."""
    return EVENTS_FILE.exists()


def up() -> bool:
    """Create events.jsonl."""
    USER_DIR.mkdir(parents=True, exist_ok=True)

    if not EVENTS_FILE.exists():
        EVENTS_FILE.touch()
        print("       Created events.jsonl")
    else:
        print("       events.jsonl already exists")

    return True
