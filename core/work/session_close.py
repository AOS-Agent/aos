#!/usr/bin/env python3
"""
AOS Session Close Hook

Runs on SessionEnd. Does two things:
1. Logs session to ~/.aos-v2/logs/sessions.jsonl (lightweight record)
2. Links session to work system:
   - If active tasks exist for this project/cwd, link the session to them
   - If a thread exists for this cwd, append the session
   - If no thread exists but we're in a project dir, create one

This is how multi-session work gets tracked automatically.
A task like "Build AOS v2 Phase C" that spans 5 sessions across days
will accumulate all those session references.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info)
- Async hook — no stdout expected
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".aos-v2" / "logs"
LOG_FILE = LOG_DIR / "sessions.jsonl"

# Add work engine to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", os.getcwd())
    transcript = hook_input.get("transcript", "")

    # --- Step 1: Log to sessions.jsonl (always, even if work system fails) ---
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "cwd": cwd,
        "event": "session_end",
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # --- Step 2: Link to work system ---
    try:
        import engine
    except ImportError:
        return  # Work engine not available, skip silently

    if session_id == "unknown":
        return  # No session ID, can't link

    # Check for a session-context file that the inject_context hook may have written
    # This file tracks which tasks were explicitly referenced during the session
    context_file = Path.home() / ".aos-v2" / "work" / ".session-context.json"
    explicit_task_ids = []
    if context_file.exists():
        try:
            ctx = json.loads(context_file.read_text())
            if ctx.get("session_id") == session_id:
                explicit_task_ids = ctx.get("task_ids", [])
            # Clean up — only valid for this session
            context_file.unlink(missing_ok=True)
        except Exception:
            pass

    # Link to explicitly referenced tasks
    for task_id in explicit_task_ids:
        engine.link_session_to_task(task_id, session_id)

    # Find active tasks for this project directory and link
    project_tasks = engine.find_tasks_by_project_or_cwd(cwd)
    active_tasks = [t for t in project_tasks if t.get("status") == "active"]
    for task in active_tasks:
        engine.link_session_to_task(task["id"], session_id)

    # Thread continuity — find or create thread for this cwd
    # Only auto-create threads for known project directories
    home = str(Path.home())
    known_project_dirs = [
        os.path.join(home, "aosv2"),
        os.path.join(home, "aos"),
        os.path.join(home, "nuchay"),
        os.path.join(home, "chief-ios-app"),
    ]
    # Also match any directory registered as a project
    is_project_dir = cwd in known_project_dirs or any(
        cwd.startswith(d) for d in known_project_dirs
    )

    if is_project_dir:
        engine.get_or_create_thread_for_cwd(cwd, session_id)


if __name__ == "__main__":
    main()
