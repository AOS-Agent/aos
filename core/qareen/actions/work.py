"""Work Actions — Governed mutations for tasks, projects, and goals.

All write operations on work items go through these actions. They are
registered with the ActionRegistry and executed via
``action_registry.execute("create_task", {...})``.
"""

from __future__ import annotations

import datetime
import uuid

from qareen.events.actions import action
from qareen.ontology.types import (
    Goal,
    KeyResult,
    ObjectType,
    Project,
    Task,
    TaskHandoff,
    TaskPriority,
    TaskStatus,
)


@action("create_task", emits="task.created")
async def create_task(
    ontology,
    title: str,
    project: str | None = None,
    priority: int = 3,
    assigned_to: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    due: str | None = None,
    parent_id: str | None = None,
    **kwargs,
) -> dict:
    """Create a new task."""
    task_id = f"{project or 'q'}#{uuid.uuid4().hex[:6]}"
    task = Task(
        id=task_id,
        title=title,
        project=project,
        priority=TaskPriority(priority),
        assigned_to=assigned_to,
        description=description,
        tags=tags or [],
        created=datetime.datetime.now(),
        created_by=kwargs.get("actor", "operator"),
        parent_id=parent_id,
    )
    if due:
        try:
            task.due = datetime.datetime.fromisoformat(due)
        except ValueError:
            pass

    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    created = adapter.create(task)
    return {
        "task_id": created.id,
        "title": created.title,
        "project": created.project,
        "priority": created.priority.value,
    }


@action("update_task", emits="task.updated")
async def update_task(ontology, task_id: str, **fields) -> dict:
    """Update task fields."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    # Filter out non-task fields
    allowed = {"title", "status", "priority", "project", "tags",
               "description", "assigned_to", "due", "started", "completed"}
    task_fields = {k: v for k, v in fields.items() if k in allowed}
    updated = adapter.update(task_id, task_fields)
    if updated is None:
        raise ValueError(f"Task not found: {task_id}")
    return {"task_id": task_id, "updated_fields": list(task_fields.keys())}


@action("complete_task", emits="task.completed")
async def complete_task(ontology, task_id: str, **kwargs) -> dict:
    """Mark a task as done."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    updated = adapter.update(
        task_id,
        {"status": "done", "completed": datetime.datetime.now().isoformat()},
    )
    if updated is None:
        raise ValueError(f"Task not found: {task_id}")
    return {"task_id": task_id, "title": updated.title}


@action("delete_task", emits="task.deleted")
async def delete_task(ontology, task_id: str, **kwargs) -> dict:
    """Delete a task."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    deleted = adapter.delete(task_id)
    if not deleted:
        raise ValueError(f"Task not found: {task_id}")
    return {"task_id": task_id, "deleted": True}


@action("write_handoff", emits="task.handoff_written")
async def write_handoff(
    ontology,
    task_id: str,
    state: str,
    next_step: str,
    files: list[str] | None = None,
    decisions: list[str] | None = None,
    blockers: list[str] | None = None,
    session_id: str | None = None,
    **kwargs,
) -> dict:
    """Write or update a task's handoff context."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    task = adapter.get(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    handoff = TaskHandoff(
        state=state,
        next_step=next_step,
        files=files or [],
        decisions=decisions or [],
        blockers=blockers or [],
        session_id=session_id,
        timestamp=datetime.datetime.now(),
    )
    adapter._upsert_handoff(task_id, handoff)
    adapter._conn.commit()
    return {"task_id": task_id, "handoff_written": True}


@action("create_project", emits="project.created")
async def create_project(
    ontology,
    id: str | None = None,
    title: str = "",
    description: str | None = None,
    path: str | None = None,
    goal: str | None = None,
    done_when: str | None = None,
    **kwargs,
) -> dict:
    """Create a new project."""
    project_id = id or uuid.uuid4().hex[:8]
    project = Project(
        id=project_id,
        title=title,
        description=description,
        path=path,
        goal=goal,
        done_when=done_when,
    )
    adapter = ontology._adapters.get(ObjectType.PROJECT)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    created = adapter.create(project)
    return {"project_id": created.id, "title": created.title}


@action("delete_project", emits="project.deleted")
async def delete_project(ontology, project_id: str, **kwargs) -> dict:
    """Delete a project."""
    adapter = ontology._adapters.get(ObjectType.PROJECT)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    deleted = adapter.delete(project_id)
    if not deleted:
        raise ValueError(f"Project not found: {project_id}")
    return {"project_id": project_id, "deleted": True}


@action("create_goal", emits="goal.created")
async def create_goal(
    ontology,
    title: str,
    weight: int = 0,
    description: str | None = None,
    key_results: list[dict] | None = None,
    project: str | None = None,
    **kwargs,
) -> dict:
    """Create a new goal."""
    goal_id = f"g_{uuid.uuid4().hex[:6]}"
    kr_list = []
    if key_results:
        for kr in key_results:
            kr_list.append(KeyResult(
                title=kr.get("title", ""),
                progress=kr.get("progress", 0),
                target=kr.get("target"),
            ))
    goal = Goal(
        id=goal_id,
        title=title,
        weight=weight,
        description=description,
        key_results=kr_list,
        project=project,
    )
    adapter = ontology._adapters.get(ObjectType.GOAL)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    created = adapter.create(goal)
    return {"goal_id": created.id, "title": created.title}


@action("create_inbox", emits="inbox.created")
async def create_inbox(
    ontology,
    content: str,
    source: str | None = None,
    **kwargs,
) -> dict:
    """Add an item to the inbox."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    inbox_id = f"in_{uuid.uuid4().hex[:8]}"
    now = datetime.datetime.now().isoformat()
    adapter._conn.execute(
        "INSERT INTO inbox (id, text, captured_at) VALUES (?, ?, ?)",
        (inbox_id, content, now),
    )
    adapter._conn.commit()
    return {"inbox_id": inbox_id, "content": content}


@action("delete_inbox", emits="inbox.deleted")
async def delete_inbox(ontology, inbox_id: str, **kwargs) -> dict:
    """Delete an inbox item."""
    adapter = ontology._adapters.get(ObjectType.TASK)
    if not adapter:
        raise RuntimeError("Work adapter not available")
    cur = adapter._conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    adapter._conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"Inbox item not found: {inbox_id}")
    return {"inbox_id": inbox_id, "deleted": True}
