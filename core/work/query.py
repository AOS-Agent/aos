"""
AOS Work Query — Filter, sort, and search tasks.

Used by skills and CLI to find relevant work items.
"""

from typing import Optional


def filter_tasks(tasks: list, status: Optional[str] = None,
                 project: Optional[str] = None, priority: Optional[int] = None,
                 assignee: Optional[str] = None, tags: Optional[list] = None,
                 energy: Optional[str] = None, context: Optional[str] = None,
                 due_before: Optional[str] = None) -> list:
    """Filter tasks by any combination of fields."""
    results = tasks

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
            # Nulls sort last for priority, first for dates
            return 999 if by == "priority" else "9999-99-99"
        return val

    return sorted(tasks, key=sort_key, reverse=reverse)


def search_tasks(tasks: list, query: str) -> list:
    """Simple text search across title, tags, notes, and project."""
    query_lower = query.lower()
    results = []
    for t in tasks:
        searchable = " ".join([
            t.get("title", ""),
            " ".join(t.get("tags", [])),
            t.get("notes", ""),
            t.get("project", ""),
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
