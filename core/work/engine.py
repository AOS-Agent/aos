"""
AOS Work Engine — Read/write work files.

Data lives at ~/.aos/work/work.yaml (small mode).
This module handles CRUD for tasks, projects, goals, threads, and inbox.
"""

import os
import yaml
from datetime import datetime, date
from pathlib import Path

WORK_DIR = Path.home() / ".aos" / "work"
WORK_FILE = WORK_DIR / "work.yaml"


def _load() -> dict:
    """Load work.yaml and return parsed dict."""
    if not WORK_FILE.exists():
        return _empty_work()
    with open(WORK_FILE, "r") as f:
        data = yaml.safe_load(f) or {}
    # Ensure all top-level keys exist
    for key in ("tasks", "projects", "goals", "threads", "inbox"):
        if key not in data:
            data[key] = []
    return data


def _save(data: dict) -> None:
    """Write work data back to work.yaml."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(WORK_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _empty_work() -> dict:
    return {
        "version": "1.0",
        "tasks": [],
        "projects": [],
        "goals": [],
        "threads": [],
        "inbox": [],
    }


def _next_id(items: list, prefix: str) -> str:
    """Generate next sequential ID (t1, t2, ... or p1, p2, ...)."""
    max_num = 0
    for item in items:
        item_id = item.get("id", "")
        if item_id.startswith(prefix):
            try:
                num = int(item_id[len(prefix):])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{prefix}{max_num + 1}"


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Task CRUD ---

def add_task(title: str, priority: int = 3, project: str = None,
             status: str = "todo", tags: list = None, source: str = "manual",
             due: str = None, energy: str = None, context: str = None) -> dict:
    """Add a new task. Returns the created task."""
    data = _load()
    task = {
        "id": _next_id(data["tasks"], "t"),
        "title": title,
        "status": status,
        "priority": priority,
        "created": _today(),
        "source": source,
    }
    if project:
        task["project"] = project
    if tags:
        task["tags"] = tags
    if due:
        task["due"] = due
    if energy:
        task["energy"] = energy
    if context:
        task["context"] = context
    data["tasks"].append(task)
    _save(data)
    return task


def complete_task(task_id: str) -> dict | None:
    """Mark a task as done. Returns the updated task or None."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            task["status"] = "done"
            task["completed"] = _now()
            _save(data)
            return task
    return None


def update_task(task_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a task. Returns updated task or None."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            for key, val in fields.items():
                if val is None and key in task:
                    del task[key]
                elif val is not None:
                    task[key] = val
            _save(data)
            return task
    return None


def start_task(task_id: str) -> dict | None:
    """Move a task to active status."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            task["status"] = "active"
            task["started"] = _now()
            _save(data)
            return task
    return None


def cancel_task(task_id: str) -> dict | None:
    """Cancel a task."""
    return update_task(task_id, status="cancelled")


def get_task(task_id: str) -> dict | None:
    """Get a single task by ID."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def get_all_tasks() -> list:
    """Get all tasks."""
    return _load()["tasks"]


# --- Project CRUD ---

def add_project(title: str, goal: str = None, done_when: str = None,
                appetite: str = None) -> dict:
    data = _load()
    project = {
        "id": _next_id(data["projects"], "p"),
        "title": title,
        "status": "active",
        "started": _today(),
    }
    if goal:
        project["goal"] = goal
    if done_when:
        project["done_when"] = done_when
    if appetite:
        project["appetite"] = appetite
    data["projects"].append(project)
    _save(data)
    return project


def get_all_projects() -> list:
    return _load()["projects"]


# --- Goal CRUD ---

def add_goal(title: str, goal_type: str = "committed", weight: float = None) -> dict:
    data = _load()
    goal = {
        "id": _next_id(data["goals"], "g"),
        "title": title,
        "status": "active",
        "type": goal_type,
    }
    if weight is not None:
        goal["weight"] = weight
    data["goals"].append(goal)
    _save(data)
    return goal


def get_all_goals() -> list:
    return _load()["goals"]


# --- Thread CRUD ---

def add_thread(title: str, session_id: str = None) -> dict:
    data = _load()
    thread = {
        "id": _next_id(data["threads"], "th"),
        "title": title,
        "status": "exploring",
        "started": _today(),
    }
    if session_id:
        thread["sessions"] = [session_id]
    data["threads"].append(thread)
    _save(data)
    return thread


def get_thread(thread_id: str) -> dict | None:
    """Get a single thread by ID."""
    data = _load()
    for thread in data["threads"]:
        if thread["id"] == thread_id:
            return thread
    return None


def update_thread(thread_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a thread."""
    data = _load()
    for thread in data["threads"]:
        if thread["id"] == thread_id:
            for key, val in fields.items():
                if val is None and key in thread:
                    del thread[key]
                elif val is not None:
                    thread[key] = val
            _save(data)
            return thread
    return None


def promote_thread(thread_id: str, project_title: str = None,
                   goal: str = None) -> dict | None:
    """Promote a thread to a project. Returns the created project."""
    data = _load()
    for thread in data["threads"]:
        if thread["id"] == thread_id:
            title = project_title or thread["title"]
            thread["status"] = "promoted"
            _save(data)
            project = add_project(title, goal=goal)
            # Link the thread to the project
            update_thread(thread_id, promoted_to=project["id"])
            return project
    return None


def get_all_threads() -> list:
    return _load()["threads"]


def find_thread_by_cwd(cwd: str) -> dict | None:
    """Find an active thread associated with a working directory."""
    data = _load()
    for thread in data["threads"]:
        if thread.get("status") in ("exploring", "active") and thread.get("cwd") == cwd:
            return thread
    return None


# --- Inbox ---

def add_inbox(text: str, source: str = "manual", confidence: float = None) -> dict:
    data = _load()
    item = {
        "id": _next_id(data["inbox"], "i"),
        "text": text,
        "captured": _now(),
        "source": source,
    }
    if confidence is not None:
        item["confidence"] = confidence
    data["inbox"].append(item)
    _save(data)
    return item


def get_inbox() -> list:
    return _load()["inbox"]


def promote_inbox(inbox_id: str, as_title: str = None) -> dict | None:
    """Promote an inbox item to a task. Returns the created task."""
    data = _load()
    for i, item in enumerate(data["inbox"]):
        if item["id"] == inbox_id:
            title = as_title or item["text"]
            data["inbox"].pop(i)
            _save(data)
            return add_task(title, source="inbox")
    return None


# --- Session Linking ---

def link_session_to_task(task_id: str, session_id: str, outcome: str = None,
                         date_str: str = None) -> dict | None:
    """Link a session to a task. Multiple sessions can link to the same task
    (multi-session work). Same session won't be linked twice."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            if "sessions" not in task:
                task["sessions"] = []
            # Dedup — don't link same session twice
            existing_ids = {s["id"] for s in task["sessions"]}
            if session_id in existing_ids:
                # Update outcome if provided
                if outcome:
                    for s in task["sessions"]:
                        if s["id"] == session_id:
                            s["outcome"] = outcome
                _save(data)
                return task
            entry = {
                "id": session_id,
                "date": date_str or _today(),
            }
            if outcome:
                entry["outcome"] = outcome
            task["sessions"].append(entry)
            _save(data)
            return task
    return None


def link_session_to_thread(thread_id: str, session_id: str,
                           notes: str = None) -> dict | None:
    """Link a session to a thread. Threads accumulate sessions across
    multiple sittings — this is the continuity layer."""
    data = _load()
    for thread in data["threads"]:
        if thread["id"] == thread_id:
            if "sessions" not in thread:
                thread["sessions"] = []
            # Dedup
            if session_id not in thread["sessions"]:
                thread["sessions"].append(session_id)
            if notes:
                existing_notes = thread.get("notes", "")
                if existing_notes:
                    thread["notes"] = existing_notes + f"\n\n[{_today()}] {notes}"
                else:
                    thread["notes"] = f"[{_today()}] {notes}"
            thread["last_session"] = _now()
            _save(data)
            return thread
    return None


def get_or_create_thread_for_cwd(cwd: str, session_id: str,
                                  title: str = None) -> dict:
    """Find an active thread for this working directory, or create one.
    This is the auto-continuity mechanism — work in the same directory
    across sessions automatically accumulates under one thread."""
    thread = find_thread_by_cwd(cwd)
    if thread:
        link_session_to_thread(thread["id"], session_id)
        return thread
    # Create new thread
    data = _load()
    # Derive title from cwd if not provided
    if not title:
        dir_name = Path(cwd).name
        title = f"Work in {dir_name}"
    thread = {
        "id": _next_id(data["threads"], "th"),
        "title": title,
        "status": "exploring",
        "started": _today(),
        "cwd": cwd,
        "sessions": [session_id],
        "last_session": _now(),
    }
    data["threads"].append(thread)
    _save(data)
    return thread


def find_tasks_by_project_or_cwd(cwd: str) -> list:
    """Find active tasks that match a working directory.
    Maps cwd to project name (e.g., ~/nuchay → 'nuchay', ~/aos → 'aos')."""
    data = _load()
    dir_name = Path(cwd).name
    # Map common directory names to project IDs
    project_aliases = {
        "aos": "aos",
        "nuchay": "nuchay",
        "chief-ios-app": "chief",
    }
    project_id = project_aliases.get(dir_name, dir_name)
    active_tasks = [t for t in data["tasks"]
                    if t.get("project") == project_id
                    and t.get("status") in ("active", "todo")]
    return active_tasks


# --- Bulk accessors ---

def load_all() -> dict:
    """Load entire work file. Use for dashboards/reviews."""
    return _load()


def summary() -> dict:
    """Quick summary stats."""
    data = _load()
    tasks = data["tasks"]
    return {
        "total_tasks": len(tasks),
        "by_status": _count_by(tasks, "status"),
        "by_priority": _count_by(tasks, "priority"),
        "projects": len(data["projects"]),
        "goals": len(data["goals"]),
        "threads": len(data["threads"]),
        "inbox": len(data["inbox"]),
    }


def _count_by(items: list, field: str) -> dict:
    counts = {}
    for item in items:
        val = str(item.get(field, "unset"))
        counts[val] = counts.get(val, 0) + 1
    return counts
