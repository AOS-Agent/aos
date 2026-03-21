#!/usr/bin/env python3
"""
AOS Work Context Injection Hook

Runs on SessionStart and PostCompact.
Reads work.yaml, finds active/due tasks and threads, outputs additionalContext JSON.

Also writes a .session-context.json file that session_close.py reads
to know which tasks were in scope during this session.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info)
- Output JSON to stdout with optional additionalContext field
"""

import json
import sys
import os
from datetime import date
from pathlib import Path

import yaml

# Add work engine to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

try:
    import engine
    import query
except ImportError:
    print(json.dumps({}))
    sys.exit(0)


def main():
    # Read hook input
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", os.getcwd())

    try:
        tasks = engine.get_all_tasks()
    except Exception:
        print(json.dumps({}))
        sys.exit(0)

    today = date.today().isoformat()

    active = query.active_tasks(tasks)
    due = query.due_today(tasks, today)
    todo_high = query.filter_tasks(
        query.filter_tasks(tasks, status="todo"),
        priority=1
    ) + query.filter_tasks(
        query.filter_tasks(tasks, status="todo"),
        priority=2
    )

    # Find tasks relevant to current working directory
    project_tasks = engine.find_tasks_by_project_or_cwd(cwd)
    project_active = [t for t in project_tasks if t.get("status") == "active"]

    # Find active thread for this directory
    current_thread = engine.find_thread_by_cwd(cwd)

    summary = engine.summary()
    inbox_count = summary["inbox"]
    thread_count = summary["threads"]

    # Build context string
    lines = []

    # Current thread (continuity)
    if current_thread:
        session_count = len(current_thread.get("sessions", []))
        lines.append(f"**Current thread**: {current_thread['id']} — {current_thread['title']} ({session_count} sessions)")
        if current_thread.get("notes"):
            # Show last note only
            last_note = current_thread["notes"].strip().split("\n\n")[-1]
            if len(last_note) > 200:
                last_note = last_note[:200] + "..."
            lines.append(f"  Last note: {last_note}")

    # Project-specific active tasks first (most relevant)
    if project_active:
        lines.append(f"**Active in this project:**")
        for t in project_active:
            sessions = len(t.get("sessions", []))
            session_info = f" ({sessions} sessions)" if sessions > 0 else ""
            lines.append(f"- {t['id']}: {t['title']}{session_info}")

    # All active tasks
    other_active = [t for t in active if t not in project_active]
    if other_active:
        lines.append("**Active (other projects):**")
        for t in other_active:
            proj = f" [{t['project']}]" if t.get("project") else ""
            lines.append(f"- {t['id']}: {t['title']}{proj}")

    if due:
        lines.append("**Due today/overdue:**")
        for t in due:
            lines.append(f"- {t['id']}: {t['title']} (due {t['due']})")

    if todo_high:
        lines.append("**High priority (todo):**")
        for t in todo_high[:5]:
            lines.append(f"- {t['id']}: {t['title']}")

    if inbox_count > 0:
        lines.append(f"**Inbox:** {inbox_count} items awaiting triage")

    # Memory proposals from reconciler
    memory_proposals_file = Path.home() / ".aos" / "work" / "memory-proposals.yaml"
    if memory_proposals_file.exists():
        try:
            proposals = yaml.safe_load(memory_proposals_file.read_text()) or []
            if proposals:
                lines.append(f"**Memory proposals ({len(proposals)}):** Significant sessions detected that may need memory updates. Review with `cat ~/.aos/work/memory-proposals.yaml` and update MEMORY.md if needed.")
        except Exception:
            pass

    if not lines:
        lines.append("No active tasks or urgent items.")

    context = "\n".join(lines)

    # Write session context file for session_close.py to read
    # Tracks which tasks were "in scope" for this session
    task_ids_in_scope = [t["id"] for t in project_active + active]
    context_file = Path.home() / ".aos" / "work" / ".session-context.json"
    try:
        context_file.parent.mkdir(parents=True, exist_ok=True)
        context_file.write_text(json.dumps({
            "session_id": session_id,
            "task_ids": task_ids_in_scope,
            "cwd": cwd,
            "thread_id": current_thread["id"] if current_thread else None,
        }))
    except Exception:
        pass  # Non-fatal

    # Behavioral guidance — this IS the always-on awareness layer
    guidance_lines = []
    if project_active:
        task_ids = ", ".join(t["id"] for t in project_active)
        guidance_lines.append(f"If you complete any active task ({task_ids}), mark it done: `python3 ~/aos/core/work/cli.py done <id>`")
    if due:
        guidance_lines.append("Overdue tasks exist — flag them to the operator if relevant.")
    guidance_lines.append("If multi-step work emerges that isn't tracked above, suggest tracking it as a task or thread.")

    guidance = "\n".join(guidance_lines)

    output = {
        "additionalContext": f"[Work System]\n{context}\n---\n{guidance}"
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
