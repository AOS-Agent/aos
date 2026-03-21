#!/usr/bin/env python3
"""
AOS Work Reconciliation Hook

Runs on Stop (async, fire-and-forget). Scans the tool use history from
the just-completed assistant turn to detect work-relevant patterns:

1. Did Claude run `cli.py done <id>`?  (explicit completion — already tracked, skip)
2. Did Claude modify files in a project dir that has active tasks?
3. Did the conversation involve multi-step work worth tracking?

This is Layer 2 of the three-layer cascade (skill -> hook -> command).
Layer 1 (work-awareness skill) handles proactive detection during conversation.
Layer 3 (explicit /work commands) is human override.

This hook bridges the gap — it catches completions and work that the skill
didn't explicitly mark.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info + tool results)
- Async hook — no stdout needed, fire-and-forget
- Must exit quickly, be idempotent, never crash
"""

import json
import os
import sys
from pathlib import Path

# Add work engine to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = Path.home() / ".aos" / "logs" / "reconcile.log"


def _log(msg: str):
    """Append to reconcile log for debugging."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            from datetime import datetime
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def main():
    # Read hook input
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "")
    cwd = hook_input.get("cwd", os.getcwd())
    tool_results = hook_input.get("tool_use_results", [])
    transcript_snippet = hook_input.get("transcript", "")

    if not session_id:
        return

    # Try to load work engine
    try:
        import engine
    except ImportError:
        _log("Work engine not available — skipping")
        return

    # Read session context written by inject_context.py
    context_file = Path.home() / ".aos" / "work" / ".session-context.json"
    task_ids_in_scope = []
    try:
        if context_file.exists():
            ctx = json.loads(context_file.read_text())
            if ctx.get("session_id") == session_id:
                task_ids_in_scope = ctx.get("task_ids", [])
    except Exception:
        pass

    if not task_ids_in_scope:
        # No tasks in scope — nothing to reconcile
        return

    # --- Pattern 1: Detect explicit cli.py done calls ---
    # If the skill/user already ran `cli.py done`, don't double-process
    already_completed = set()
    for result in tool_results:
        tool_input = result.get("input", "")
        if isinstance(tool_input, dict):
            tool_input = tool_input.get("command", "")
        if "cli.py done" in str(tool_input) or "cli.py done" in str(result.get("command", "")):
            # Extract task ID from the command
            parts = str(tool_input).split("done")
            if len(parts) > 1:
                task_id = parts[1].strip().split()[0] if parts[1].strip() else ""
                if task_id:
                    already_completed.add(task_id)

    # --- Pattern 2: Detect file modifications in project directories ---
    files_modified = []
    for result in tool_results:
        tool_name = result.get("tool_name", "")
        if tool_name in ("Write", "Edit", "NotebookEdit"):
            file_path = ""
            inp = result.get("input", {})
            if isinstance(inp, dict):
                file_path = inp.get("file_path", "")
            if file_path:
                files_modified.append(file_path)

    # --- Pattern 3: Link session to active tasks if meaningful work happened ---
    if files_modified:
        for task_id in task_ids_in_scope:
            if task_id not in already_completed:
                # Link the session with a summary of what was modified
                file_count = len(files_modified)
                sample = ", ".join(Path(f).name for f in files_modified[:3])
                if file_count > 3:
                    sample += f" +{file_count - 3} more"
                engine.link_session_to_task(
                    task_id, session_id,
                    outcome=f"Modified {file_count} files: {sample}"
                )
        _log(f"Linked {len(files_modified)} file changes to {len(task_ids_in_scope)} tasks")

    # --- Pattern 4: Detect bash commands that indicate task completion ---
    completion_signals = []
    for result in tool_results:
        tool_name = result.get("tool_name", "")
        inp = result.get("input", {})
        if isinstance(inp, dict):
            command = inp.get("command", "")
        else:
            command = str(inp)

        # Deployment, test passes, build successes
        if tool_name == "Bash":
            lower_cmd = command.lower()
            if any(sig in lower_cmd for sig in [
                "deploy", "npm run build", "pytest", "cargo test",
                "git push", "make install", "swift build"
            ]):
                completion_signals.append(command[:80])

    if completion_signals:
        _log(f"Completion signals detected: {len(completion_signals)}")
        # Don't auto-complete tasks — just log for awareness
        # The work-awareness skill or human should explicitly mark done


if __name__ == "__main__":
    main()
