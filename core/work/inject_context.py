#!/usr/bin/env python3
"""
AOS Work Context Injection Hook

Runs on SessionStart and PostCompact.
Reads work.yaml, finds active/due tasks, outputs additionalContext JSON.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info)
- Output JSON to stdout with optional additionalContext field
- additionalContext appears in the session context after compaction
"""

import json
import sys
import os
from datetime import date

# Add work engine to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

try:
    import engine
    import query
except ImportError:
    # If engine isn't available, output empty context
    print(json.dumps({}))
    sys.exit(0)

def main():
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

    summary = engine.summary()
    inbox_count = summary["inbox"]

    # Build context string
    lines = []

    if active:
        lines.append("**Active tasks:**")
        for t in active:
            proj = f" [{t['project']}]" if t.get("project") else ""
            lines.append(f"- {t['id']}: {t['title']}{proj}")

    if due:
        lines.append("**Due today/overdue:**")
        for t in due:
            lines.append(f"- {t['id']}: {t['title']} (due {t['due']})")

    if todo_high:
        lines.append("**High priority (todo):**")
        for t in todo_high[:5]:  # Cap at 5
            lines.append(f"- {t['id']}: {t['title']}")

    if inbox_count > 0:
        lines.append(f"**Inbox:** {inbox_count} items awaiting triage")

    if not lines:
        lines.append("No active tasks or urgent items.")

    context = "\n".join(lines)

    output = {
        "additionalContext": f"[Work System]\n{context}\nManage with /work skill. Review with /review skill."
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
