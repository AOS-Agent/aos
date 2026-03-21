"""
AOS Event Bus — simple append-only event coordination.

Usage:
    from core.lib.events import emit, recent

    # Emit an event
    emit("session-exported", source="session-export", data={"count": 3})

    # Read recent events
    for event in recent(hours=1):
        print(event["event"], event["data"])

    # Read events of a specific type
    for event in recent(event_type="task-completed", hours=24):
        print(event)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

EVENTS_FILE = Path.home() / ".aos" / "events.jsonl"


def emit(event: str, source: str = "unknown", data: dict = None):
    """Append an event to the log."""
    entry = {
        "ts": datetime.now().isoformat(),
        "event": event,
        "source": source,
        "data": data or {},
    }
    with open(EVENTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def recent(hours: int = 24, event_type: str = None, limit: int = 100) -> list[dict]:
    """Read recent events, optionally filtered by type."""
    if not EVENTS_FILE.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=hours)
    results = []

    with open(EVENTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["ts"])
                if ts < cutoff:
                    continue
                if event_type and entry.get("event") != event_type:
                    continue
                results.append(entry)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    return results[-limit:]
