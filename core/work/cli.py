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
