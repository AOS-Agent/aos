#!/usr/bin/env python3
"""
AOS Work Reconciliation Hook — Live Context Edition

Runs on Stop (async, fire-and-forget). Reads the LIVE CONTEXT to know what
task is currently being worked on — no inference, no pattern matching needed.

If a task is active (via `work start`):
  → Attribute file modifications to that task immediately
  → Detect completion signals for awareness

If no task is active but work happened:
  → Log it as untracked for visibility

The live context is set by `work start` and cleared by `work done`/`work stop`.
This hook just reads it — making the Stop hook trivial and reliable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)
_work_dir = os.path.abspath(os.path.join(_this_dir, '..', '..', 'work'))
sys.path.insert(0, _work_dir)

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


def _iter_tool_uses(transcript_path: str):
    """Yield (tool_name, input_dict) for every tool_use block in the transcript.

    Claude Code's Stop hook payload does NOT include a `tool_use_results`
    field (verified against the installed CLI binary's string table -- it
    has no occurrence anywhere). What it does provide is `transcript_path`,
    the session's own JSONL history: one line per message, assistant
    messages carry a `content` list, and any block with
    `type == "tool_use"` is a tool call this session made. That is the real
    source for "what files did this turn touch."
    """
    if not transcript_path:
        return
    path = Path(transcript_path)
    if not path.is_file():
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield block.get("name", ""), (block.get("input") or {})
    except OSError:
        return


def main():
    # Read hook input
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "")
    hook_input.get("cwd", os.getcwd())
    tool_uses = list(_iter_tool_uses(hook_input.get("transcript_path", "")))

    if not session_id:
        return

    # Try to load work engine (ontology backend)
    try:
        import backend as engine
    except ImportError:
        _log("Work engine not available — skipping")
        return

    # ── Read live context (the workbench) ──────────────
    ctx = engine.get_live_context()

    # ── Extract what happened this turn ────────────────
    files_modified = []
    already_completed = set()

    for tool_name, inp in tool_uses:
        # Track file modifications
        if tool_name in ("Write", "Edit", "NotebookEdit"):
            if isinstance(inp, dict):
                fp = inp.get("file_path", "")
                if fp:
                    files_modified.append(fp)

        # Track explicit `work done` calls so we don't double-process
        if tool_name == "Bash":
            command = inp.get("command", "") if isinstance(inp, dict) else str(inp)
            if "cli.py done" in command:
                parts = command.split("done")
                if len(parts) > 1:
                    tid = parts[1].strip().split()[0] if parts[1].strip() else ""
                    if tid:
                        already_completed.add(tid)

    # ── Case 1: Active task — attribute work directly ──
    if ctx:
        task_id = ctx["task_id"]

        if files_modified and task_id not in already_completed:
            file_count = len(files_modified)
            sample = ", ".join(Path(f).name for f in files_modified[:3])
            if file_count > 3:
                sample += f" +{file_count - 3} more"

            engine.link_session_to_task(
                task_id, session_id,
                outcome=f"Modified {file_count} files: {sample}"
            )
            _log(f"[live] Attributed {file_count} files to {task_id}")

        elif files_modified:
            _log(f"[live] {task_id} already completed via CLI — skipping attribution")

        return  # Done — live context handled everything

    # ── Case 2: No active task — check for untracked work ──
    if files_modified:
        _log(f"[untracked] {len(files_modified)} files modified with no active task: {', '.join(Path(f).name for f in files_modified[:5])}")
        # Log to activity so dashboard can surface it
        try:
            engine._log_activity(
                "session_untracked",
                detail=f"{len(files_modified)} files modified without active task"
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
