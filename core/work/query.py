"""
AOS Work Query — Filter, sort, search, and tree queries.

Used by skills, CLI, and dashboard to find relevant work items.
v2: Subtask trees, progress rollup, handoff-aware queries.
"""

from typing import Optional


def filter_tasks(tasks: list, status: Optional[str] = None,
                 project: Optional[str] = None, priority: Optional[int] = None,
                 assignee: Optional[str] = None, tags: Optional[list] = None,
                 energy: Optional[str] = None, context: Optional[str] = None,
                 due_before: Optional[str] = None,
                 top_level_only: bool = False) -> list:
    """Filter tasks by any combination of fields."""
    results = tasks

    if top_level_only:
        results = [t for t in results if not t.get("parent")]

    if status:
        # Support comma-separated statuses: "todo,active"
        statuses = [s.strip() for s in status.split(",")]
        results = [t for t in results if t.get("status") in statuses]

    if project:
        results = [t for t in results if t.get("project") == project]

    if priority is not None:
        results = [t for t in results if t.get("priority") == priority]

    if assignee:
        results = [t for t in results if t.get("assignee") == assignee]

    if tags:
        results = [t for t in results if
                   any(tag in t.get("tags", []) for tag in tags)]

    if energy:
        results = [t for t in results if t.get("energy") == energy]

    if context:
        results = [t for t in results if t.get("context") == context]

    if due_before:
        results = [t for t in results if
                   t.get("due") and t["due"] <= due_before]

    return results


def sort_tasks(tasks: list, by: str = "priority", reverse: bool = False) -> list:
    """Sort tasks by a field. Priority sorts 1-first (urgent first)."""
    def sort_key(t):
        val = t.get(by)
        if val is None:
            return 999 if by == "priority" else "9999-99-99"
        return val

    return sorted(tasks, key=sort_key, reverse=reverse)


def search_tasks(tasks: list, query: str) -> list:
    """Simple text search across title, tags, notes, project, and ID."""
    query_lower = query.lower()
    results = []
    for t in tasks:
        searchable = " ".join([
            t.get("title", ""),
            " ".join(t.get("tags", [])),
            t.get("notes", ""),
            t.get("project", ""),
            t.get("id", ""),
        ]).lower()
        if query_lower in searchable:
            results.append(t)
    return results


def active_tasks(tasks: list) -> list:
    """Get tasks currently in progress."""
    return filter_tasks(tasks, status="active")


def due_today(tasks: list, today: str) -> list:
    """Get tasks due today or overdue."""
    return [t for t in tasks
            if t.get("due") and t["due"] <= today
            and t.get("status") not in ("done", "cancelled")]


def blocked_tasks(tasks: list) -> list:
    """Get tasks that are blocked by other incomplete tasks."""
    done_ids = {t["id"] for t in tasks if t.get("status") == "done"}
    results = []
    for t in tasks:
        blocked_by = t.get("blocked_by", [])
        if blocked_by and not all(bid in done_ids for bid in blocked_by):
            results.append(t)
    return results


# ── Tree Queries ──────────────────────────────────────

def build_task_trees(tasks: list) -> list:
    """Build task trees — attach subtasks to their parents.

    Returns list of top-level tasks, each with a 'subtasks' key.
    """
    # Index by ID
    by_id = {t["id"]: dict(t) for t in tasks}

    # Attach subtasks
    for t_id, t in by_id.items():
        t["subtasks"] = []

    for t in tasks:
        parent_id = t.get("parent")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["subtasks"].append(by_id[t["id"]])

    # Return only top-level tasks
    return [by_id[t["id"]] for t in tasks if not t.get("parent")]


def task_progress(task: dict, all_tasks: list) -> dict:
    """Compute progress for a task based on its subtasks.

    Returns: {total: N, done: N, pct: 0-100, has_subtasks: bool}
    """
    subtasks = [t for t in all_tasks if t.get("parent") == task["id"]]
    if not subtasks:
        # Leaf task — progress is binary
        is_done = task.get("status") == "done"
        return {
            "total": 1,
            "done": 1 if is_done else 0,
            "pct": 100 if is_done else 0,
            "has_subtasks": False,
        }

    done = sum(1 for t in subtasks if t.get("status") == "done")
    total = len(subtasks)
    return {
        "total": total,
        "done": done,
        "pct": round(done / total * 100) if total > 0 else 0,
        "has_subtasks": True,
    }


def project_progress(project_id: str, tasks: list) -> dict:
    """Compute progress for an entire project.

    Returns: {total: N, done: N, active: N, todo: N, pct: 0-100}
    """
    project_tasks = [t for t in tasks
                     if t.get("project") == project_id and not t.get("parent")]
    if not project_tasks:
        return {"total": 0, "done": 0, "active": 0, "todo": 0, "pct": 0}

    done = sum(1 for t in project_tasks if t.get("status") == "done")
    active = sum(1 for t in project_tasks if t.get("status") == "active")
    todo = sum(1 for t in project_tasks if t.get("status") == "todo")
    total = len(project_tasks)

    return {
        "total": total,
        "done": done,
        "active": active,
        "todo": todo,
        "pct": round(done / total * 100) if total > 0 else 0,
    }


def tasks_with_handoffs(tasks: list) -> list:
    """Get tasks that have handoff context (work in progress with continuity)."""
    return [t for t in tasks if t.get("handoff")]


def stale_handoffs(tasks: list, days_threshold: int = 3) -> list:
    """Get tasks with handoff context that hasn't been updated recently."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days_threshold)).isoformat()
    results = []
    for t in tasks:
        handoff = t.get("handoff")
        if handoff and handoff.get("updated", "") < cutoff:
            if t.get("status") in ("active", "todo"):
                results.append(t)
    return results
