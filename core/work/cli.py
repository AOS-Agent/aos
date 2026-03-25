#!/usr/bin/env python3
"""
AOS Work CLI — v2 with project-scoped IDs, fuzzy resolution, subtasks, and handoffs.

Usage:
    work add "Buy groceries"
    work add "Fix login" --priority 2 --project website
    work add "Fix login" (auto-detects project from cwd)
    work list
    work list --project aos-v2
    work done "sse push"              (fuzzy resolve)
    work done aos#3                   (exact ID)
    work start 3                      (scoped to cwd project)
    work subtask aos#3 "Base variables and typography"
    work handoff aos#3 --state "Extracted base vars..." --next "Component styles"
    work show aos#3                   (shows task + subtasks + handoff)
    work migrate                      (migrate old t1,t2 IDs to new format)
"""

import sys
import os
import json

# Add parent dir to path so we can import engine/query
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine
import query


# ── Resolution helpers ────────────────────────────────

def _resolve(query_str: str, require: bool = True) -> dict | None:
    """Resolve a task from user input. Supports exact ID, fuzzy title, and scoped shorthand."""
    # Detect project from cwd for scoped resolution
    project_id = engine.detect_project_from_cwd()
    task = engine.resolve_task_in_project(query_str, project_id)

    if not task and require:
        print(f"Could not resolve task: '{query_str}'")
        # Suggest close matches
        tasks = engine.get_all_tasks()
        matches = query.search_tasks(tasks, query_str)
        if matches:
            print("  Did you mean:")
            for m in matches[:3]:
                print(f"    {m['id']}  {m['title']}")
        sys.exit(1)

    return task


def _auto_project() -> str | None:
    """Auto-detect project from current directory."""
    return engine.detect_project_from_cwd()


# ── Commands ──────────────────────────────────────────

def cmd_add(args):
    if not args:
        print("Usage: add <title> [--priority N] [--project ID] [--tags t1,t2] [--due DATE] [--energy low|medium|high]")
        sys.exit(1)

    title_parts = []
    priority = 3
    project = _auto_project()  # Auto-detect from cwd
    tags = None
    due = None
    energy = None
    status = "todo"
    source_ref = None

    i = 0
    while i < len(args):
        if args[i] == "--priority" and i + 1 < len(args):
            priority = int(args[i + 1])
            i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]  # Explicit overrides auto-detect
            i += 2
        elif args[i] == "--no-project":
            project = None
            i += 1
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
        elif args[i] == "--source-ref" and i + 1 < len(args):
            source_ref = args[i + 1]
            i += 2
        else:
            title_parts.append(args[i])
            i += 1

    title = " ".join(title_parts)
    if not title:
        print("Error: title is required")
        sys.exit(1)

    task = engine.add_task(title, priority=priority, project=project,
                           tags=tags, due=due, energy=energy, status=status,
                           source_ref=source_ref)
    proj_info = f" [{task.get('project', '')}]" if task.get("project") else ""
    print(f"Created {task['id']}: {task['title']}{proj_info}")


def cmd_done(args):
    if not args:
        print("Usage: done <task_id or search>")
        sys.exit(1)
    query_str = " ".join(args)
    task = _resolve(query_str)
    result = engine.complete_task(task["id"])
    if result:
        print(f"Completed {result['id']}: {result['title']}")
        if result.get("auto_completed"):
            print(f"  (auto-completed by subtask cascade)")
    else:
        print(f"Task {task['id']} not found")
        sys.exit(1)


def cmd_start(args):
    if not args:
        print("Usage: start <task_id or search>")
        sys.exit(1)
    query_str = " ".join(args)
    task = _resolve(query_str)
    result = engine.start_task(task["id"])
    if result:
        print(f"Started {result['id']}: {result['title']}")
    else:
        print(f"Task {task['id']} not found")
        sys.exit(1)


def cmd_cancel(args):
    if not args:
        print("Usage: cancel <task_id or search>")
        sys.exit(1)
    query_str = " ".join(args)
    task = _resolve(query_str)
    result = engine.cancel_task(task["id"])
    if result:
        print(f"Cancelled {result['id']}: {result['title']}")
    else:
        print(f"Task {task['id']} not found")
        sys.exit(1)


def cmd_show(args):
    if not args:
        print("Usage: show <task_id or search>")
        sys.exit(1)
    query_str = " ".join(args)
    task = _resolve(query_str)
    tree = engine.get_task_tree(task["id"])
    if not tree:
        print(f"Task not found")
        sys.exit(1)

    # Display task details
    print(f"\n  {tree['id']}  {tree['title']}")
    print(f"  {'=' * 50}")
    print(f"  Status:   {tree.get('status', '?')}")
    print(f"  Priority: P{tree.get('priority', 3)}")
    if tree.get("source_ref"):
        print(f"  Initiative: {tree['source_ref']}")
    if tree.get("project"):
        print(f"  Project:  {tree['project']}")
    if tree.get("created"):
        print(f"  Created:  {tree['created']}")
    if tree.get("started"):
        print(f"  Started:  {tree['started']}")
    if tree.get("completed"):
        print(f"  Done:     {tree['completed']}")
    if tree.get("due"):
        print(f"  Due:      {tree['due']}")
    if tree.get("sessions"):
        print(f"  Sessions: {len(tree['sessions'])}")

    # Subtasks
    subtasks = tree.get("subtasks", [])
    if subtasks:
        done = sum(1 for s in subtasks if s.get("status") == "done")
        print(f"\n  Subtasks ({done}/{len(subtasks)}):")
        for sub in subtasks:
            marker = "x" if sub.get("status") == "done" else ">" if sub.get("status") == "active" else " "
            print(f"    [{marker}] {sub['id']}  {sub['title']}")

    # Handoff
    handoff = tree.get("handoff")
    if handoff:
        print(f"\n  Handoff (updated {handoff.get('updated', '?')}):")
        if handoff.get("state"):
            for line in handoff["state"].strip().split("\n"):
                print(f"    {line}")
        if handoff.get("next_step"):
            print(f"\n  Next step:")
            for line in handoff["next_step"].strip().split("\n"):
                print(f"    {line}")
        if handoff.get("files_touched"):
            print(f"\n  Files: {', '.join(handoff['files_touched'])}")
        if handoff.get("decisions"):
            print(f"\n  Decisions:")
            for d in handoff["decisions"]:
                print(f"    - {d}")
        if handoff.get("blockers"):
            print(f"\n  Blockers:")
            for b in handoff["blockers"]:
                print(f"    ! {b}")

    print()


def cmd_list(args):
    tasks = engine.get_all_tasks()

    # Parse filters
    status = None
    project = None
    priority = None
    sort_by = "priority"
    show_all = False

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
        elif args[i] == "--all":
            show_all = True
            i += 1
        else:
            i += 1

    # Auto-detect project from cwd if not specified
    if not project and not show_all:
        auto_proj = _auto_project()
        # Don't auto-filter — just note it for display
        # (show all tasks, but highlight the current project)

    if status:
        tasks = query.filter_tasks(tasks, status=status)
    if project:
        tasks = query.filter_tasks(tasks, project=project)
    if priority is not None:
        tasks = query.filter_tasks(tasks, priority=priority)

    # Default: hide done and cancelled unless explicitly requested
    if not status:
        tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")]

    # Build trees (subtasks nested under parents)
    trees = query.build_task_trees(tasks)
    trees = query.sort_tasks(trees, by=sort_by)

    if not trees:
        print("No tasks found.")
        return

    # Group by project for display
    by_project = {}
    unassigned = []
    for t in trees:
        proj = t.get("project")
        if proj:
            by_project.setdefault(proj, []).append(t)
        else:
            unassigned.append(t)

    # Display
    all_tasks = engine.get_all_tasks()
    print()
    for proj_id, proj_tasks in sorted(by_project.items()):
        # Find project title
        projects = engine.get_all_projects()
        proj_title = proj_id
        for p in projects:
            if p["id"] == proj_id:
                proj_title = p.get("title", proj_id)
                break
        progress = query.project_progress(proj_id, all_tasks)
        pct = int(progress["done"] / progress["total"] * 100) if progress["total"] else 0
        print(f"  {proj_title}")
        print(f"  {progress['done']}/{progress['total']} done ({pct}%)")
        print()
        _print_task_list(proj_tasks)
        print()

    if unassigned:
        print(f"  Unassigned")
        print()
        _print_task_list(unassigned)
        print()


def _print_task_list(tasks: list, indent: int = 2):
    """Print tasks with clean formatting."""
    STATUS = {"active": "\033[34m>\033[0m", "todo": " ", "done": "\033[32m\u2713\033[0m", "cancelled": "\033[90m-\033[0m"}
    PRIORITY_COLOR = {1: "\033[31m", 2: "\033[33m", 3: "", 4: "\033[90m"}
    RESET = "\033[0m"
    DIM = "\033[90m"

    for t in tasks:
        status = t.get("status", "todo")
        p = t.get("priority", 3)
        icon = STATUS.get(status, " ")
        p_color = PRIORITY_COLOR.get(p, "")

        # Title — dim if done
        title = t["title"]
        if status == "done":
            title = f"{DIM}{title}{RESET}"
        elif p_color:
            title = f"{p_color}{title}{RESET}"

        # Metadata chips
        chips = []
        if p <= 2:
            chips.append(f"{PRIORITY_COLOR[p]}P{p}{RESET}")
        sessions = len(t.get("sessions", []))
        if sessions:
            chips.append(f"{DIM}{sessions}s{RESET}")
        if t.get("handoff"):
            chips.append(f"\033[35m\u2192{RESET}")
        if t.get("due"):
            chips.append(f"{DIM}due {t['due']}{RESET}")

        subtasks = t.get("subtasks", [])
        if subtasks:
            sub_done = sum(1 for s in subtasks if s.get("status") == "done")
            chips.append(f"{DIM}{sub_done}/{len(subtasks)}{RESET}")

        chip_str = f"  {' '.join(chips)}" if chips else ""
        tid = f"{DIM}{t['id']}{RESET}"

        prefix = " " * indent
        print(f"{prefix}  {icon}  {title}{chip_str}  {tid}")

        # Subtasks
        for sub in subtasks:
            sub_icon = STATUS.get(sub.get("status", "todo"), " ")
            sub_title = sub["title"]
            if sub.get("status") == "done":
                sub_title = f"{DIM}{sub_title}{RESET}"
            sub_id = f"{DIM}{sub['id']}{RESET}"
            print(f"{prefix}     {sub_icon}  {sub_title}  {sub_id}")


def cmd_subtask(args):
    """Add a subtask to an existing task."""
    if len(args) < 2:
        print("Usage: subtask <parent_id or search> <title> [--done] [--active]")
        sys.exit(1)

    parent_query = args[0]
    title_parts = []
    status = "todo"
    priority = None

    i = 1
    while i < len(args):
        if args[i] == "--done":
            status = "done"
            i += 1
        elif args[i] == "--active":
            status = "active"
            i += 1
        elif args[i] == "--priority" and i + 1 < len(args):
            priority = int(args[i + 1])
            i += 2
        else:
            title_parts.append(args[i])
            i += 1

    title = " ".join(title_parts)
    if not title:
        print("Error: subtask title is required")
        sys.exit(1)

    parent = _resolve(parent_query)
    sub = engine.add_subtask(parent["id"], title, priority=priority, status=status)
    if sub:
        print(f"Created {sub['id']}: {sub['title']} (under {parent['id']})")
    else:
        print(f"Failed to create subtask")
        sys.exit(1)


def cmd_handoff(args):
    """Write handoff context for a task."""
    if not args:
        print("Usage: handoff <task_id or search> --state '...' [--next '...'] [--files f1,f2] [--decisions d1,d2] [--blockers b1,b2]")
        sys.exit(1)

    task_query = args[0]
    state = None
    next_step = None
    files = None
    decisions = None
    blockers = None

    i = 1
    while i < len(args):
        if args[i] == "--state" and i + 1 < len(args):
            state = args[i + 1]
            i += 2
        elif args[i] == "--next" and i + 1 < len(args):
            next_step = args[i + 1]
            i += 2
        elif args[i] == "--files" and i + 1 < len(args):
            files = [f.strip() for f in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--decisions" and i + 1 < len(args):
            decisions = [d.strip() for d in args[i + 1].split("|")]
            i += 2
        elif args[i] == "--blockers" and i + 1 < len(args):
            blockers = [b.strip() for b in args[i + 1].split("|")]
            i += 2
        else:
            i += 1

    if not state:
        print("Error: --state is required")
        sys.exit(1)

    task = _resolve(task_query)
    result = engine.write_handoff(
        task["id"], state=state, next_step=next_step,
        files_touched=files, decisions=decisions, blockers=blockers
    )
    if result:
        print(f"Handoff written for {result['id']}: {result['title']}")
    else:
        print(f"Failed to write handoff")
        sys.exit(1)


def cmd_dispatch(args):
    """Generate a dispatch prompt for a task (for Chief to inject into agent prompts)."""
    if not args:
        print("Usage: dispatch <task_id or search>")
        sys.exit(1)
    query_str = " ".join(args)
    task = _resolve(query_str)
    prompt = engine.build_handoff_prompt(task["id"])
    if prompt:
        print(prompt)
    else:
        print(f"No dispatch prompt available for {task['id']}")


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
        proj = f" [{t['project']}]" if t.get("project") else ""
        print(f"  {t['id']:12s}  {t['status']:9s}  {t['title']}{proj}")


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
    all_tasks = engine.get_all_tasks()
    for p in projects:
        status = p.get("status", "?")
        goal = f" -> {p['goal']}" if p.get("goal") else ""
        progress = query.project_progress(p["id"], all_tasks)
        pct = f" ({progress['pct']}%)" if progress['total'] > 0 else ""
        counts = f" [{progress['done']}/{progress['total']}]"
        print(f"  {p['id']:12s}  {status:10s}  {p['title']}{goal}{counts}{pct}")


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

    target_query = args[0]
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
        context_file = os.path.join(os.path.expanduser("~"), ".aos", "work", ".session-context.json")
        if os.path.exists(context_file):
            try:
                with open(context_file) as f:
                    ctx = json.load(f)
                session_id = ctx.get("session_id")
            except Exception:
                pass
        if not session_id:
            print("Error: --session ID required (no active session detected)")
            sys.exit(1)

    if target_query.startswith("th"):
        result = engine.link_session_to_thread(target_query, session_id, notes=outcome)
        if result:
            count = len(result.get("sessions", []))
            print(f"Linked session to thread {target_query}: {result['title']} ({count} sessions total)")
        else:
            print(f"Thread {target_query} not found")
            sys.exit(1)
    else:
        task = _resolve(target_query)
        result = engine.link_session_to_task(task["id"], session_id, outcome=outcome)
        if result:
            count = len(result.get("sessions", []))
            print(f"Linked session to task {result['id']}: {result['title']} ({count} sessions)")
        else:
            print(f"Task not found")
            sys.exit(1)


def cmd_thread(args):
    """Create or manage threads."""
    if not args:
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
        print(f"Promoted thread {thread_id} -> project {project['id']}: {project['title']}")
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
        promoted = f" -> {t['promoted_to']}" if t.get("promoted_to") else ""
        print(f"  {t['id']:6s}  {t['status']:10s}  {t['title']}  ({sessions} sessions){promoted}")


def cmd_metrics(args):
    """Show flow metrics for current week."""
    import metrics as work_metrics

    data = engine.load_all()
    tasks = data["tasks"]
    goals = data["goals"]

    week = work_metrics.compute_current_week(tasks)
    goal_health = work_metrics.compute_goal_health(goals, tasks)

    save = "--no-save" not in args
    if save:
        path = work_metrics.save_weekly_snapshot(week)

    print(work_metrics.format_metrics_display(week, goal_health))

    if save:
        print(f"\n  Snapshot saved: {path}")


def cmd_today(args):
    """Show today's work plan."""
    from datetime import date as _date
    today = _date.today().isoformat()
    tasks = engine.get_all_tasks()

    due = query.due_today(tasks, today)
    active = query.active_tasks(tasks)
    high_todo = query.filter_tasks(
        query.filter_tasks(tasks, status="todo"), priority=1
    ) + query.filter_tasks(
        query.filter_tasks(tasks, status="todo"), priority=2
    )

    # Tasks with stale handoffs
    stale = query.stale_handoffs(tasks)

    seen = set()
    sections = []

    if due:
        section_tasks = [t for t in due if t["id"] not in seen]
        for t in section_tasks:
            seen.add(t["id"])
        if section_tasks:
            sections.append(("Due today / overdue", section_tasks))

    if active:
        section_tasks = [t for t in active if t["id"] not in seen]
        for t in section_tasks:
            seen.add(t["id"])
        if section_tasks:
            sections.append(("In progress", section_tasks))

    if high_todo:
        section_tasks = [t for t in high_todo if t["id"] not in seen]
        for t in section_tasks:
            seen.add(t["id"])
        if section_tasks:
            sections.append(("High priority (ready)", section_tasks))

    if stale:
        section_tasks = [t for t in stale if t["id"] not in seen]
        for t in section_tasks:
            seen.add(t["id"])
        if section_tasks:
            sections.append(("Stale handoffs (needs attention)", section_tasks))

    if not sections:
        print("Nothing pressing today. Check `work list` for all tasks.")
        return

    print(f"Today -- {today}")
    print("=" * 40)
    for label, items in sections:
        print(f"\n  {label}:")
        for t in items:
            p = t.get("priority", 0)
            marker = {1: "!!", 2: "!", 3: " ", 4: "~", 0: "?"}.get(p, " ")
            proj = f" [{t['project']}]" if t.get("project") else ""
            due_str = f" (due {t['due']})" if t.get("due") else ""
            sessions = len(t.get("sessions", []))
            sess_str = f" [{sessions}s]" if sessions > 0 else ""
            handoff_str = " *" if t.get("handoff") else ""
            print(f"    {marker} {t['id']:12s}  {t['title']}{proj}{due_str}{sess_str}{handoff_str}")


def cmd_next(args):
    """Suggest what to work on next based on priority, energy, and context."""
    tasks = engine.get_all_tasks()

    # Only top-level tasks
    candidates = [t for t in tasks
                  if t.get("status") in ("todo", "active") and not t.get("parent")]
    if not candidates:
        print("No tasks to suggest." if not engine.get_inbox() else
              f"No tasks, but {len(engine.get_inbox())} inbox items to triage.")
        return

    def score(t):
        s = 0
        p = t.get("priority", 3)
        s += (5 - p) * 10

        if t.get("status") == "active":
            s += 15

        from datetime import date as _date
        today = _date.today().isoformat()
        if t.get("due") and t["due"] <= today:
            s += 25

        sessions = len(t.get("sessions", []))
        if sessions > 0:
            s += min(sessions * 3, 12)

        # Boost tasks with handoff (continuity)
        if t.get("handoff"):
            s += 10

        return s

    ranked = sorted(candidates, key=score, reverse=True)
    top = ranked[:3]

    print("Suggested next:")
    print("=" * 40)
    for i, t in enumerate(top, 1):
        p = t.get("priority", 0)
        marker = {1: "!!", 2: "!", 3: " ", 4: "~", 0: "?"}.get(p, " ")
        proj = f" [{t['project']}]" if t.get("project") else ""
        status = t.get("status", "?")
        sessions = len(t.get("sessions", []))
        sess_str = f" ({sessions} sessions)" if sessions > 0 else ""
        reasons = []
        if status == "active":
            reasons.append("in progress")
        if t.get("handoff"):
            reasons.append("has handoff")
        if t.get("due"):
            from datetime import date as _date
            if t["due"] <= _date.today().isoformat():
                reasons.append("overdue")
            else:
                reasons.append(f"due {t['due']}")
        if sessions > 0:
            reasons.append("has momentum")
        reason_str = f"  -- {', '.join(reasons)}" if reasons else ""
        print(f"  {i}. {marker} {t['id']:12s}  {t['title']}{proj}{sess_str}{reason_str}")


def cmd_drift(args):
    """Show drift analysis."""
    import metrics as work_metrics

    data = engine.load_all()
    drift = work_metrics.compute_drift(data["goals"], data["tasks"])
    print(work_metrics.format_drift_display(drift))


def cmd_migrate(args):
    """Migrate old t1,t2,... IDs to new project-scoped format."""
    print("Migrating task IDs to project-scoped format...")
    id_map = engine.migrate_task_ids()
    if not id_map:
        print("  No tasks need migration (already using new format).")
        return
    print(f"  Migrated {len(id_map)} tasks:")
    for old, new in id_map.items():
        print(f"    {old} -> {new}")
    print("\n  Old IDs are preserved as _legacy_id for backward compatibility.")


def cmd_json(args):
    """Output full work data as JSON (for programmatic use)."""
    data = engine.load_all()
    print(json.dumps(data, indent=2, default=str))


def cmd_initiatives(args):
    """List active initiatives from vault/knowledge/initiatives/.

    Scans each initiative doc for phase completion by counting checked/unchecked
    boxes under ### Phase N headers. Shows real progress, not just frontmatter.
    """
    import glob as globmod
    import re as _re
    init_dir = os.path.join(os.path.expanduser("~"), "vault", "knowledge", "initiatives")
    if not os.path.isdir(init_dir):
        print("No initiatives directory found.")
        return

    try:
        import yaml as _yaml
    except ImportError:
        print("yaml not available")
        return

    files = sorted(
        globmod.glob(os.path.join(init_dir, "*.md")),
        key=os.path.getmtime, reverse=True
    )
    if not files:
        print("No initiatives found.")
        return

    status_emoji = {
        "research": "🔬", "shaping": "🔲", "planning": "📐",
        "executing": "⚡", "review": "📝", "done": "✅", "archived": "📦"
    }

    show_all = "--all" in args

    count = 0
    for fpath in files:
        try:
            with open(fpath) as f:
                raw = f.read()
            fm_end = raw.find("---", 3)
            if fm_end == -1:
                continue
            fm = _yaml.safe_load(raw[3:fm_end])
            if not fm:
                continue
            status = fm.get("status", "unknown")
            if not show_all and status in ("done", "archived"):
                continue

            emoji = status_emoji.get(status, "📋")
            title = fm.get("title", os.path.basename(fpath))
            appetite = fm.get("appetite", "—")
            updated = fm.get("updated", "?")

            # Parse phase progress from actual checkbox counts
            body = raw[fm_end + 3:]
            phases = []
            # Match ### Phase N: Name or ### Phase 1: Foundation [wave: 1] — ✅ DONE
            phase_blocks = _re.split(r'(?=^### Phase \d)', body, flags=_re.MULTILINE)
            for block in phase_blocks:
                header_match = _re.match(r'^### Phase (\d+)[:\s]+(.+?)(?:\n|$)', block)
                if not header_match:
                    continue
                phase_num = header_match.group(1)
                phase_title = header_match.group(2).strip()
                # Clean up title — remove [wave: N] and status markers
                phase_title = _re.sub(r'\[wave:.*?\]', '', phase_title).strip()
                phase_title = _re.sub(r'—\s*$', '', phase_title).strip()

                done_count = len(_re.findall(r'^\s*- \[x\]', block, _re.MULTILINE))
                todo_count = len(_re.findall(r'^\s*- \[ \]', block, _re.MULTILINE))
                total = done_count + todo_count
                is_done = "✅ DONE" in header_match.group(0) or (total > 0 and done_count == total)
                phases.append({
                    "num": phase_num,
                    "title": phase_title,
                    "done": done_count,
                    "total": total,
                    "complete": is_done,
                })

            # Build output
            phases_done = sum(1 for p in phases if p["complete"])
            total_phases = len(phases) if phases else 0
            total_tasks = sum(p["total"] for p in phases)
            total_done = sum(p["done"] for p in phases)

            if count == 0:
                print(f"\n  {'All' if show_all else 'Active'} Initiatives")
                print(f"  {'=' * 60}")

            # Header line
            pct = int(total_done / total_tasks * 100) if total_tasks else 0
            progress_bar = _progress_bar(pct)
            stale_warn = ""
            if status not in ("done", "archived") and updated and updated != "?":
                try:
                    from datetime import date
                    last = date.fromisoformat(str(updated))
                    days_ago = (date.today() - last).days
                    if days_ago > 3:
                        stale_warn = f" ⚠️ stale ({days_ago}d)"
                except (ValueError, TypeError):
                    pass

            print(f"\n  {emoji} {title}")
            print(f"    Status: {status} | Appetite: {appetite} | Updated: {updated}{stale_warn}")
            if phases:
                print(f"    Progress: {progress_bar} {pct}% ({total_done}/{total_tasks} tasks, {phases_done}/{total_phases} phases)")
                for p in phases:
                    if p["complete"]:
                        mark = "✅"
                    elif p["done"] > 0:
                        mark = "🔶"
                    else:
                        mark = "⬜"
                    p_pct = int(p["done"] / p["total"] * 100) if p["total"] else 0
                    print(f"      {mark} Phase {p['num']}: {p['title']} ({p['done']}/{p['total']})")

            count += 1
        except Exception:
            continue

    if count == 0:
        print("\n  No active initiatives.")
    print()


def _progress_bar(pct):
    """Render a 10-char progress bar."""
    filled = int(pct / 10)
    return "▓" * filled + "░" * (10 - filled)


def cmd_briefing(args):
    """One-shot briefing: tasks, initiatives, inbox, schedule. For mid-session 'what's next'."""
    import re as _re
    from datetime import date

    try:
        import yaml as _yaml
    except ImportError:
        _yaml = None

    # --- Tasks ---
    tasks = engine.get_all_tasks()
    active = [t for t in tasks if t.get("status") == "active"]
    todo_high = [t for t in tasks if t.get("status") == "todo" and t.get("priority", 5) <= 2]
    today_str = date.today().isoformat()
    due = [t for t in tasks if t.get("due") and t.get("due") <= today_str and t.get("status") not in ("done", "cancelled")]
    s = engine.summary()

    print("=== Work Briefing ===\n")

    # Helper: subtask progress
    def _sub_info(parent_id):
        subs = [t for t in tasks if t.get("parent") == parent_id]
        if not subs:
            return ""
        done = sum(1 for s in subs if s.get("status") == "done")
        return f" ({done}/{len(subs)} parts)"

    # Helper: initiative linkage
    def _init_ref(t):
        ref = t.get("source_ref", "")
        if ref and "initiatives/" in ref:
            return f" → {ref.split('initiatives/')[-1].replace('.md', '')}"
        return ""

    # Active tasks
    if active:
        print("Active:")
        for t in active:
            proj = f" [{t['project']}]" if t.get("project") else ""
            sub = _sub_info(t["id"])
            init = _init_ref(t)
            print(f"  {t['id']}: {t['title']}{proj}{sub}{init}")

    # High priority todos
    if todo_high:
        print("High priority:")
        for t in todo_high[:5]:
            proj = f" [{t['project']}]" if t.get("project") else ""
            sub = _sub_info(t["id"])
            init = _init_ref(t)
            print(f"  {t['id']}: {t['title']}{proj}{sub}{init}")

    # Due/overdue
    if due:
        print("Due/overdue:")
        for t in due:
            print(f"  {t['id']}: {t['title']} (due {t['due']})")

    # Inbox
    inbox = engine.get_inbox()
    if inbox:
        print(f"\nInbox ({len(inbox)}):")
        for item in inbox[:3]:
            text = item.get("text", str(item)) if isinstance(item, dict) else str(item)
            print(f"  - {text[:80]}")

    # --- Initiatives ---
    init_dir = os.path.join(os.path.expanduser("~"), "vault", "knowledge", "initiatives")
    if os.path.isdir(init_dir) and _yaml:
        import glob as _globmod
        files = sorted(_globmod.glob(os.path.join(init_dir, "*.md")), key=os.path.getmtime, reverse=True)
        inits = []
        for fpath in files[:5]:
            try:
                with open(fpath) as f:
                    raw = f.read(3000)
                if not raw.startswith("---"):
                    continue
                fm_end = raw.find("---", 3)
                if fm_end == -1:
                    continue
                fm = _yaml.safe_load(raw[3:fm_end])
                if not fm or fm.get("status") in ("done", "archived"):
                    continue
                # Get next action from state digest
                next_action = ""
                ds = raw.find("## State Digest")
                if ds != -1:
                    block = raw[ds:ds + 500]
                    na = _re.search(r'Next action:\s*(.+)', block)
                    if na:
                        next_action = na.group(1).strip()
                inits.append({
                    "title": fm.get("title", "?"),
                    "status": fm.get("status", "?"),
                    "next": next_action,
                })
            except Exception:
                continue
        if inits:
            print(f"\nInitiatives ({len(inits)}):")
            emoji = {"research": "🔬", "shaping": "🔲", "planning": "📐", "executing": "⚡", "review": "📝"}
            for i in inits:
                e = emoji.get(i["status"], "📋")
                line = f"  {e} {i['title']} [{i['status']}]"
                if i["next"]:
                    line += f" — {i['next'][:80]}"
                print(line)

    # --- Schedule ---
    if _yaml:
        try:
            op_file = os.path.join(os.path.expanduser("~"), ".aos", "config", "operator.yaml")
            with open(op_file) as f:
                op = _yaml.safe_load(f) or {}
            blocks = op.get("schedule", {}).get("blocks", [])
            day_name = date.today().strftime("%a").lower()
            today_blocks = [b for b in blocks if day_name in b.get("days", [])]
            if today_blocks:
                print("\nSchedule today:")
                for b in today_blocks:
                    print(f"  {b['name']}: {b.get('start', '?')}–{b.get('end', '?')}")
        except Exception:
            pass

    # --- Suggested focus ---
    suggestions = []
    # Handoff tasks first
    handoff = [t for t in (active or todo_high) if t.get("handoff")]
    if handoff:
        t = handoff[0]
        h = t["handoff"]
        suggestions.append(f"Resume {t['id']}: {t['title']}" + (f" — {h.get('next_step', '')[:60]}" if h.get('next_step') else ""))
    if due:
        suggestions.append(f"Due: {due[0]['id']}: {due[0]['title']}")
    if todo_high and not any(t in handoff for t in todo_high):
        suggestions.append(f"{todo_high[0]['id']}: {todo_high[0]['title']}")

    if suggestions:
        print("\nSuggested focus:")
        for idx, sug in enumerate(suggestions[:3], 1):
            print(f"  {idx}. {sug}")

    print(f"\nTotals: {s['total_tasks']} tasks | {s['projects']} projects | {s['goals']} goals | {s['threads']} threads | {s['inbox']} inbox")


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
    "today": cmd_today,
    "next": cmd_next,
    "metrics": cmd_metrics,
    "drift": cmd_drift,
    "link": cmd_link,
    "thread": cmd_thread,
    "threads": cmd_threads,
    "promote": cmd_promote,
    "subtask": cmd_subtask,
    "handoff": cmd_handoff,
    "dispatch": cmd_dispatch,
    "migrate": cmd_migrate,
    "json": cmd_json,
    "initiatives": cmd_initiatives,
    "briefing": cmd_briefing,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: work <command> [args]")
        print(f"Commands: {', '.join(sorted(COMMANDS.keys()))}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(sorted(COMMANDS.keys()))}")
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
