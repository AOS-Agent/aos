#!/usr/bin/env python3
"""
AOS Session Close Hook

Runs on SessionEnd.
Logs basic session info to ~/.aos-v2/logs/sessions.jsonl.

This is the lightweight version. The full session-export pipeline
(converting JSONL sessions to vault markdown) runs as a cron job.
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".aos-v2" / "logs"
LOG_FILE = LOG_DIR / "sessions.jsonl"


def main():
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    # Extract what we can from hook input
    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", os.getcwd())

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "cwd": cwd,
        "event": "session_end",
    }

    # Append to log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()
