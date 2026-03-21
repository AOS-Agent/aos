#!/usr/bin/env python3
"""
AOS Work CLI — Thin wrapper around the work engine.

Usage:
    python3 cli.py add "Buy groceries"
    python3 cli.py add "Fix login" --priority 2 --project website
    python3 cli.py list
    python3 cli.py list --status active
    python3 cli.py list --project aos-v2
    python3 cli.py done t1
    python3 cli.py start t2
    python3 cli.py cancel t3
    python3 cli.py show t1
    python3 cli.py search "groceries"
    python3 cli.py summary
    python3 cli.py inbox "Look into WebSocket approach"
    python3 cli.py projects
    python3 cli.py goals
"""

import sys
import os
import json

# Add parent dir to path so we can import engine/query
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine
import query


def cmd_add(args):
    if not args:
        print("Usage: add <title> [--priority N] [--project ID] [--tags t1,t2] [--due DATE] [--energy low|medium|high]")
        sys.exit(1)

    title_parts = []
    priority = 3
    project = None
    tags = None
    due = None
    energy = None
    status = "todo"

    i = 0
    while i < len(args):
        if args[i] == "--priority" and i + 1 < len(args):
            priority = int(args[i + 1])
            i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            tags = args[i + 1].split(",")
            i += 2
        elif args[i] == "--due" and i + 1 < len(args):
            due = args[i + 1]
            i += 2
        elif args[i] == "--energy" and i + 1 < len(args):
            energy = args[i + 1]
            i += 2
        elif args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]
            i += 2
        else:
            title_parts.append(args[i])
            i += 1

    title = " ".join(title_parts)
    if not title:
        print("Error: title is required")
        sys.exit(1)

    task = engine.add_task(title, priority=priority, project=project,
                           tags=tags, due=due, energy=energy, status=status)
    print(f"Created {task['id']}: {task['title']} [{task['status']}]")


def cmd_done(args):
    if not args:
        print("Usage: done <task_id>")
        sys.exit(1)
    task = engine.complete_task(args[0])
    if task:
        print(f"Completed {task['id']}: {task['title']}")
    else:
        print(f"Task {args[0]} not found")
        sys.exit(1)


def cmd_start(args):
    if not args:
        print("Usage: start <task_id>")
        sys.exit(1)
    task = engine.start_task(args[0])
    if task:
        print(f"Started {task['id']}: {task['title']}")
    else:
        print(f"Task {args[0]} not found")
        sys.exit(1)


def cmd_cancel(args):
    if not args:
        print("Usage: cancel <task_id>")
        sys.exit(1)
    task = engine.cancel_task(args[0])
    if task:
        print(f"Cancelled {task['id']}: {task['title']}")
    else:
        print(f"Task {args[0]} not found")
        sys.exit(1)


def cmd_show(args):
    if not args:
        print("Usage: show <task_id>")
        sys.exit(1)
    task = engine.get_task(args[0])
    if task:
        for key, val in task.items():
            print(f"  {key}: {val}")
    else:
        print(f"Task {args[0]} not found")
        sys.exit(1)


def cmd_list(args):
    tasks = engine.get_all_tasks()

    # Parse filters
    status = None
    project = None
    priority = None
    sort_by = "priority"

    i = 0
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]
            i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif args[i] == "--priority" and i + 1 < len(args):
            priority = int(args[i + 1])
            i += 2
        elif args[i] == "--sort" and i + 1 < len(args):
            sort_by = args[i + 1]
            i += 2
        else:
            i += 1

    if status:
        tasks = query.filter_tasks(tasks, status=status)
    if project:
        tasks = query.filter_tasks(tasks, project=project)
    if priority is not None:
        tasks = query.filter_tasks(tasks, priority=priority)

    # Default: hide done and cancelled unless explicitly requested
    if not status:
        tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")]

    tasks = query.sort_tasks(tasks, by=sort_by)

    if not tasks:
        print("No tasks found.")
        return

    # Print formatted list
    for t in tasks:
        p = t.get("priority", 0)
        priority_marker = {1: "!!", 2: "!", 3: " ", 4: "~", 0: "?"}
        marker = priority_marker.get(p, " ")
        proj = f" [{t['project']}]" if t.get("project") else ""
        due = f" (due {t['due']})" if t.get("due") else ""
        print(f"  {marker} {t['id']:4s}  {t['status']:9s}  {t['title']}{proj}{due}")


def cmd_search(args):
    if not args:
        print("Usage: search <query>")
        sys.exit(1)
    tasks = engine.get_all_tasks()
    results = query.search_tasks(tasks, " ".join(args))
    if not results:
        print("No matching tasks.")
        return
    for t in results:
        print(f"  {t['id']:4s}  {t['status']:9s}  {t['title']}")


def cmd_summary(args):
    s = engine.summary()
    print(f"Tasks: {s['total_tasks']}  |  Projects: {s['projects']}  |  Goals: {s['goals']}  |  Threads: {s['threads']}  |  Inbox: {s['inbox']}")
    if s["by_status"]:
        parts = [f"{k}: {v}" for k, v in sorted(s["by_status"].items())]
        print(f"  Status: {', '.join(parts)}")


def cmd_inbox(args):
    if not args:
        # Show inbox
        items = engine.get_inbox()
        if not items:
            print("Inbox is empty.")
            return
        for item in items:
            print(f"  {item['id']:4s}  {item['text']}  ({item['source']}, {item['captured'][:10]})")
        return

    # Add to inbox
    text = " ".join(args)
    item = engine.add_inbox(text)
    print(f"Captured {item['id']}: {item['text']}")


def cmd_projects(args):
    projects = engine.get_all_projects()
    if not projects:
        print("No projects.")
        return
    for p in projects:
        status = p.get("status", "?")
        goal = f" -> {p['goal']}" if p.get("goal") else ""
        print(f"  {p['id']:12s}  {status:10s}  {p['title']}{goal}")


def cmd_goals(args):
    goals = engine.get_all_goals()
    if not goals:
        print("No goals.")
        return
    for g in goals:
        weight = f" (w={g['weight']})" if g.get("weight") else ""
        print(f"  {g['id']:20s}  {g['status']:10s}  {g['title']}{weight}")


def cmd_link(args):
    """Link a session to a task or thread."""
    if len(args) < 1:
        print("Usage: link <task_id|thread_id> [--session ID] [--outcome TEXT]")
        sys.exit(1)

    target_id = args[0]
    session_id = None
    outcome = None

    i = 1
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif args[i] == "--outcome" and i + 1 < len(args):
            outcome = args[i + 1]
            i += 2
        else:
            i += 1

    if not session_id:
        # Try to read current session from context file
        context_file = os.path.join(os.path.expanduser("~"), ".aos-v2", "work", ".session-context.json")
        if os.path.exists(context_file):
            try:
                ctx = json.load(open(context_file))
                session_id = ctx.get("session_id")
            except Exception:
                pass
        if not session_id:
            print("Error: --session ID required (no active session detected)")
            sys.exit(1)

    if target_id.startswith("th"):
        result = engine.link_session_to_thread(target_id, session_id, notes=outcome)
        if result:
            count = len(result.get("sessions", []))
            print(f"Linked session to thread {target_id}: {result['title']} ({count} sessions total)")
        else:
            print(f"Thread {target_id} not found")
            sys.exit(1)
    else:
        result = engine.link_session_to_task(target_id, session_id, outcome=outcome)
        if result:
            count = len(result.get("sessions", []))
            print(f"Linked session to task {target_id}: {result['title']} ({count} sessions)")
        else:
            print(f"Task {target_id} not found")
            sys.exit(1)


def cmd_thread(args):
    """Create or manage threads."""
    if not args:
        # List active threads
        threads = engine.get_all_threads()
        active = [t for t in threads if t.get("status") in ("exploring", "active")]
        if not active:
            print("No active threads.")
            return
        for t in active:
            sessions = len(t.get("sessions", []))
            last = t.get("last_session", t.get("started", ""))[:10]
            cwd_short = os.path.basename(t["cwd"]) if t.get("cwd") else ""
            print(f"  {t['id']:6s}  {t['status']:10s}  {t['title']}  ({sessions} sessions, last: {last}) {cwd_short}")
        return

    # Create new thread
    title = " ".join(args)
    thread = engine.add_thread(title)
    print(f"Created {thread['id']}: {thread['title']} [{thread['status']}]")


def cmd_promote(args):
    """Promote a thread to a project."""
    if not args:
        print("Usage: promote <thread_id> [--title PROJECT_TITLE] [--goal GOAL_ID]")
        sys.exit(1)

    thread_id = args[0]
    title = None
    goal = None

    i = 1
    while i < len(args):
        if args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        elif args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        else:
            i += 1

    project = engine.promote_thread(thread_id, project_title=title, goal=goal)
    if project:
        print(f"Promoted thread {thread_id} → project {project['id']}: {project['title']}")
    else:
        print(f"Thread {thread_id} not found")
        sys.exit(1)


def cmd_threads(args):
    """List all threads (including inactive)."""
    threads = engine.get_all_threads()
    if not threads:
        print("No threads.")
        return
    for t in threads:
        sessions = len(t.get("sessions", []))
        promoted = f" → {t['promoted_to']}" if t.get("promoted_to") else ""
        print(f"  {t['id']:6s}  {t['status']:10s}  {t['title']}  ({sessions} sessions){promoted}")


def cmd_json(args):
    """Output full work data as JSON (for programmatic use)."""
    data = engine.load_all()
    print(json.dumps(data, indent=2, default=str))


COMMANDS = {
    "add": cmd_add,
    "done": cmd_done,
    "start": cmd_start,
    "cancel": cmd_cancel,
    "show": cmd_show,
    "list": cmd_list,
    "search": cmd_search,
    "summary": cmd_summary,
    "inbox": cmd_inbox,
    "projects": cmd_projects,
    "goals": cmd_goals,
    "link": cmd_link,
    "thread": cmd_thread,
    "threads": cmd_threads,
    "promote": cmd_promote,
    "json": cmd_json,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: work <command> [args]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
