"""Qareen API — Work routes.

Task, project, goal, and inbox management endpoints.
Ported from the legacy dashboard into typed FastAPI routes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path, Request, status
from fastapi.responses import JSONResponse

from ..ontology.types import ObjectType, TaskPriority, TaskStatus
from .schemas import (
    CreateGoalRequest,
    CreateInboxRequest,
    CreateProjectRequest,
    CreateTaskRequest,
    GoalListResponse,
    GoalResponse,
    InboxItemResponse,
    KeyResultSchema,
    ProjectListResponse,
    ProjectResponse,
    TaskHandoffSchema,
    TaskListResponse,
    TaskResponse,
    UpdateTaskRequest,
    WorkResponse,
    WriteHandoffRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["work"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_to_response(task) -> TaskResponse:
    """Convert a Task ontology object to a TaskResponse schema."""
    handoff = None
    if getattr(task, "handoff", None):
        handoff = TaskHandoffSchema(
            state=task.handoff.state,
            next_step=task.handoff.next_step,
            files=task.handoff.files or [],
            decisions=task.handoff.decisions or [],
            blockers=task.handoff.blockers or [],
            session_id=task.handoff.session_id,
            timestamp=task.handoff.timestamp,
        )
    return TaskResponse(
        id=task.id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        project=task.project,
        tags=task.tags or [],
        description=task.description,
        assigned_to=task.assigned_to,
        created_by=task.created_by,
        created=task.created,
        started=task.started,
        completed=task.completed,
        due=task.due,
        parent_id=task.parent_id,
        subtask_ids=task.subtask_ids or [],
        handoff=handoff,
        pipeline=task.pipeline,
        pipeline_stage=task.pipeline_stage,
        recurrence=task.recurrence,
    )


def _project_to_response(project) -> ProjectResponse:
    """Convert a Project ontology object to a ProjectResponse schema."""
    return ProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        status=project.status or "active",
        path=project.path,
        goal=project.goal,
        done_when=project.done_when,
        stages=project.stages if project.stages else None,
        current_stage=project.current_stage,
        task_count=project.task_count or 0,
        done_count=project.done_count or 0,
        active_count=project.active_count or 0,
    )


def _goal_to_response(goal) -> GoalResponse:
    """Convert a Goal ontology object to a GoalResponse schema."""
    krs = []
    for kr in (goal.key_results or []):
        krs.append(KeyResultSchema(
            title=kr.title,
            progress=kr.progress,
            target=kr.target,
        ))
    return GoalResponse(
        id=goal.id,
        title=goal.title,
        weight=goal.weight,
        description=goal.description,
        key_results=krs,
        project=goal.project,
    )


# ---------------------------------------------------------------------------
# Full work state
# ---------------------------------------------------------------------------


@router.get("/work", response_model=WorkResponse)
async def get_work(request: Request) -> WorkResponse:
    """Return full work state: tasks, projects, goals, inbox."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return WorkResponse()

    # Tasks
    tasks = ontology.list(ObjectType.TASK, limit=200)
    task_responses = [_task_to_response(t) for t in tasks]
    by_status: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for t in tasks:
        s = t.status.value if hasattr(t.status, "value") else str(t.status)
        by_status[s] = by_status.get(s, 0) + 1
        if t.project:
            by_project[t.project] = by_project.get(t.project, 0) + 1

    task_list = TaskListResponse(
        tasks=task_responses,
        total=len(task_responses),
        by_status=by_status,
        by_project=by_project,
    )

    # Projects
    projects = ontology.list(ObjectType.PROJECT, limit=100)
    project_responses = [_project_to_response(p) for p in projects]
    project_list = ProjectListResponse(
        projects=project_responses,
        total=len(project_responses),
    )

    # Goals
    goals = ontology.list(ObjectType.GOAL, limit=50)
    goal_responses = [_goal_to_response(g) for g in goals]
    total_weight = sum(g.weight for g in goals)
    goal_list = GoalListResponse(
        goals=goal_responses,
        total_weight=total_weight,
    )

    # Inbox
    inbox_items: list[InboxItemResponse] = []
    raw_inbox = ontology.list(ObjectType.TASK, filters={"_type": "inbox"}, limit=50)
    if raw_inbox:
        for item in raw_inbox:
            if isinstance(item, dict):
                inbox_items.append(InboxItemResponse(
                    id=item.get("id", ""),
                    content=item.get("text", item.get("content", "")),
                    created=item.get("captured_at"),
                    source=item.get("source", item.get("project_id")),
                ))

    # Next task suggestion: first active or first todo task
    next_task = None
    for t in tasks:
        if t.status == TaskStatus.ACTIVE:
            next_task = _task_to_response(t)
            break
    if next_task is None:
        for t in tasks:
            if t.status == TaskStatus.TODO:
                next_task = _task_to_response(t)
                break

    return WorkResponse(
        tasks=task_list,
        projects=project_list,
        goals=goal_list,
        inbox=inbox_items,
        next_task=next_task,
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(body: CreateTaskRequest, request: Request) -> TaskResponse | JSONResponse:
    """Create a new task."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("create_task", {
        "ontology": ontology,
        "title": body.title,
        "project": body.project,
        "priority": body.priority.value if body.priority else 3,
        "assigned_to": body.assigned_to,
        "description": body.description,
        "tags": body.tags or [],
        "due": body.due.isoformat() if body.due else None,
        "parent_id": body.parent_id,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    # Fetch the created task
    task_id = result["result"]["task_id"]
    task = ontology.get(ObjectType.TASK, task_id)
    if task:
        return _task_to_response(task)
    # Fallback
    return TaskResponse(
        id=task_id,
        title=body.title,
        status=TaskStatus.TODO,
        priority=body.priority or TaskPriority.NORMAL,
        project=body.project,
    )


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    body: UpdateTaskRequest,
    request: Request,
    task_id: str = Path(..., description="Project-scoped task ID, e.g. aos#42"),
) -> TaskResponse | JSONResponse:
    """Update fields on an existing task."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    fields: dict[str, Any] = {"ontology": ontology, "task_id": task_id}
    update_data = body.model_dump(exclude_none=True)
    # Convert enums to values
    if "status" in update_data:
        update_data["status"] = update_data["status"].value if hasattr(update_data["status"], "value") else update_data["status"]
    if "priority" in update_data:
        update_data["priority"] = update_data["priority"].value if hasattr(update_data["priority"], "value") else update_data["priority"]
    fields.update(update_data)

    result = await registry.execute("update_task", fields, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    task = ontology.get(ObjectType.TASK, task_id)
    if task:
        return _task_to_response(task)
    return JSONResponse({"error": "Task not found after update"}, status_code=404)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    request: Request,
    task_id: str = Path(..., description="Task ID to delete"),
) -> None:
    """Delete a task."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("delete_task", {
        "ontology": ontology,
        "task_id": task_id,
    }, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)


@router.post(
    "/tasks/{task_id}/subtasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subtask(
    body: CreateTaskRequest,
    request: Request,
    task_id: str = Path(..., description="Parent task ID"),
) -> TaskResponse | JSONResponse:
    """Create a subtask under an existing task."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("create_task", {
        "ontology": ontology,
        "title": body.title,
        "project": body.project,
        "priority": body.priority.value if body.priority else 3,
        "assigned_to": body.assigned_to,
        "description": body.description,
        "tags": body.tags or [],
        "due": body.due.isoformat() if body.due else None,
        "parent_id": task_id,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    new_task_id = result["result"]["task_id"]
    task = ontology.get(ObjectType.TASK, new_task_id)
    if task:
        return _task_to_response(task)
    return TaskResponse(id=new_task_id, title=body.title)


@router.put("/tasks/{task_id}/handoff", response_model=TaskHandoffSchema)
async def write_handoff(
    body: WriteHandoffRequest,
    request: Request,
    task_id: str = Path(..., description="Task to write handoff for"),
) -> TaskHandoffSchema | JSONResponse:
    """Write or update a task's handoff context for agent continuity."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("write_handoff", {
        "ontology": ontology,
        "task_id": task_id,
        "state": body.state,
        "next_step": body.next_step,
        "files": body.files,
        "decisions": body.decisions,
        "blockers": body.blockers,
        "session_id": body.session_id,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    return TaskHandoffSchema(
        state=body.state,
        next_step=body.next_step,
        files=body.files,
        decisions=body.decisions,
        blockers=body.blockers,
        session_id=body.session_id,
    )


@router.get("/tasks/{task_id}/dispatch", response_model=TaskHandoffSchema)
async def get_dispatch(
    request: Request,
    task_id: str = Path(..., description="Task to get dispatch context for"),
) -> TaskHandoffSchema | JSONResponse:
    """Get the dispatch prompt (handoff context) for picking up a task."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    task = ontology.get(ObjectType.TASK, task_id)
    if not task:
        return JSONResponse({"error": f"Task not found: {task_id}"}, status_code=404)

    if not task.handoff:
        return JSONResponse({"error": f"No handoff context for task: {task_id}"}, status_code=404)

    return TaskHandoffSchema(
        state=task.handoff.state,
        next_step=task.handoff.next_step,
        files=task.handoff.files or [],
        decisions=task.handoff.decisions or [],
        blockers=task.handoff.blockers or [],
        session_id=task.handoff.session_id,
        timestamp=task.handoff.timestamp,
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(body: CreateProjectRequest, request: Request) -> ProjectResponse | JSONResponse:
    """Create a new project."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("create_project", {
        "ontology": ontology,
        "id": body.id,
        "title": body.title,
        "description": body.description,
        "path": body.path,
        "goal": body.goal,
        "done_when": body.done_when,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    project_id = result["result"]["project_id"]
    project = ontology.get(ObjectType.PROJECT, project_id)
    if project:
        return _project_to_response(project)
    return ProjectResponse(id=project_id, title=body.title)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
) -> ProjectResponse | JSONResponse:
    """Update a project's fields."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    # Read request body directly since no schema defined for project updates
    project = ontology.get(ObjectType.PROJECT, project_id)
    if not project:
        return JSONResponse({"error": f"Project not found: {project_id}"}, status_code=404)
    return _project_to_response(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    request: Request,
    project_id: str = Path(..., description="Project ID to delete"),
) -> None:
    """Delete a project."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("delete_project", {
        "ontology": ontology,
        "project_id": project_id,
    }, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.post("/goals", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(body: CreateGoalRequest, request: Request) -> GoalResponse | JSONResponse:
    """Create a new goal."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    kr_dicts = [kr.model_dump() for kr in body.key_results] if body.key_results else []

    result = await registry.execute("create_goal", {
        "ontology": ontology,
        "title": body.title,
        "weight": body.weight,
        "description": body.description,
        "key_results": kr_dicts,
        "project": body.project,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    goal_id = result["result"]["goal_id"]
    goal = ontology.get(ObjectType.GOAL, goal_id)
    if goal:
        return _goal_to_response(goal)
    return GoalResponse(id=goal_id, title=body.title, weight=body.weight)


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


@router.post("/inbox", response_model=InboxItemResponse, status_code=status.HTTP_201_CREATED)
async def create_inbox_item(body: CreateInboxRequest, request: Request) -> InboxItemResponse | JSONResponse:
    """Add an item to the inbox for later triage."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("create_inbox", {
        "ontology": ontology,
        "content": body.content,
        "source": body.source,
    }, actor="operator")

    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    return InboxItemResponse(
        id=result["result"]["inbox_id"],
        content=body.content,
        source=body.source,
    )


@router.delete("/inbox/{inbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox_item(
    request: Request,
    inbox_id: str = Path(..., description="Inbox item ID to delete"),
) -> None:
    """Delete an inbox item."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute("delete_inbox", {
        "ontology": ontology,
        "inbox_id": inbox_id,
    }, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)


# ---------------------------------------------------------------------------
# Task list with server-side filtering, sorting, pagination
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def list_tasks(
    request: Request,
    status: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    assignee: str | None = None,
    search: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    overdue: bool = False,
    sort: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    """List tasks with server-side filtering, sorting, and pagination.

    Query params:
      status=todo,active       Multi-value status filter
      priority=1,2             Multi-value priority filter
      project=nuchay           Project filter
      assignee=alex            Assignee filter
      search=keyword           Full-text search
      due_before=2026-04-10    Due date range
      due_after=2026-04-01
      overdue=true             Overdue only
      sort=priority:asc,due_at:asc   Sort keys
      limit=100                Page size
      offset=0                 Offset
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"tasks": [], "total": 0})

    # Build filters dict for the adapter
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status.split(",")
    if priority:
        filters["priority"] = [int(p) for p in priority.split(",")]
    if project:
        filters["project_id"] = project
    if assignee:
        filters["assigned_to"] = assignee
    if search:
        filters["search"] = search

    # Fetch from ontology (adapter handles filtering)
    tasks = ontology.list(ObjectType.TASK, filters=filters, limit=limit, offset=offset)

    # Post-filter for date ranges (adapter may not support these)
    from datetime import datetime
    if due_before:
        tasks = [t for t in tasks if t.due and t.due <= due_before]
    if due_after:
        tasks = [t for t in tasks if t.due and t.due >= due_after]
    if overdue:
        now = datetime.now().isoformat()
        tasks = [t for t in tasks if t.due and t.due < now and t.status not in ("done", "cancelled")]

    # Sort
    if sort:
        for sort_key in reversed(sort.split(",")):
            parts = sort_key.strip().split(":")
            field = parts[0]
            direction = parts[1] if len(parts) > 1 else "asc"
            reverse = direction == "desc"
            try:
                tasks.sort(key=lambda t: getattr(t, field, "") or "", reverse=reverse)
            except Exception:
                pass

    total = len(tasks)
    responses = [_task_to_response(t) for t in tasks]

    return JSONResponse({
        "tasks": [r.model_dump(mode="json") for r in responses],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.get("/tasks/{task_id}", response_model=None)
async def get_task(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> TaskResponse | JSONResponse:
    """Get a single task with full detail."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    task = ontology.get(ObjectType.TASK, task_id)
    if not task:
        return JSONResponse({"error": f"Task not found: {task_id}"}, status_code=404)

    return _task_to_response(task)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/comments")
async def list_comments(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> JSONResponse:
    """List comments on a task."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"comments": []})

    # Direct DB query for comments
    db = getattr(ontology, "_work_adapter", None)
    if not db or not hasattr(db, "conn"):
        return JSONResponse({"comments": []})

    try:
        cursor = db.conn.execute(
            "SELECT id, entity_type, entity_id, parent_id, author_id, author_type, body, created_at, modified_at, is_edited "
            "FROM comments WHERE entity_type = 'task' AND entity_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        comments = [
            {
                "id": row[0], "entity_type": row[1], "entity_id": row[2],
                "parent_id": row[3], "author_id": row[4], "author_type": row[5],
                "body": row[6], "created_at": row[7], "modified_at": row[8], "is_edited": bool(row[9]),
            }
            for row in cursor.fetchall()
        ]
        return JSONResponse({"comments": comments})
    except Exception as e:
        logger.error(f"Failed to list comments: {e}")
        return JSONResponse({"comments": []})


@router.post("/tasks/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> JSONResponse:
    """Add a comment to a task."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    body = await request.json()
    comment_body = body.get("body", "").strip()
    if not comment_body:
        return JSONResponse({"error": "Comment body is required"}, status_code=400)

    author_id = body.get("author_id", "operator")
    author_type = body.get("author_type", "operator")

    db = getattr(ontology, "_work_adapter", None)
    if not db or not hasattr(db, "conn"):
        return JSONResponse({"error": "Database not available"}, status_code=503)

    import uuid
    from datetime import datetime
    comment_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    try:
        db.conn.execute(
            "INSERT INTO comments (id, entity_type, entity_id, author_id, author_type, body, created_at) "
            "VALUES (?, 'task', ?, ?, ?, ?, ?)",
            (comment_id, task_id, author_id, author_type, comment_body, now),
        )
        db.conn.commit()
        return JSONResponse({
            "id": comment_id, "entity_type": "task", "entity_id": task_id,
            "author_id": author_id, "author_type": author_type,
            "body": comment_body, "created_at": now,
        }, status_code=201)
    except Exception as e:
        logger.error(f"Failed to create comment: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Activity stream (history + comments unified)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/activity")
async def get_activity(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> JSONResponse:
    """Get unified activity stream: field changes + comments."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"activity": []})

    db = getattr(ontology, "_work_adapter", None)
    if not db or not hasattr(db, "conn"):
        return JSONResponse({"activity": []})

    try:
        activity = []

        # History entries
        cursor = db.conn.execute(
            "SELECT field_name, old_value, new_value, actor, actor_type, timestamp "
            "FROM entity_history WHERE entity_type = 'task' AND entity_id = ? "
            "ORDER BY timestamp ASC",
            (task_id,),
        )
        for row in cursor.fetchall():
            activity.append({
                "type": "change", "field": row[0], "old_value": row[1],
                "new_value": row[2], "actor": row[3], "actor_type": row[4],
                "timestamp": row[5],
            })

        # Comments
        cursor = db.conn.execute(
            "SELECT id, author_id, author_type, body, created_at "
            "FROM comments WHERE entity_type = 'task' AND entity_id = ? "
            "ORDER BY created_at ASC",
            (task_id,),
        )
        for row in cursor.fetchall():
            activity.append({
                "type": "comment", "id": row[0], "actor": row[1],
                "actor_type": row[2], "body": row[3], "timestamp": row[4],
            })

        # Sort by timestamp
        activity.sort(key=lambda a: a.get("timestamp", ""))

        return JSONResponse({"activity": activity})
    except Exception as e:
        logger.error(f"Failed to get activity: {e}")
        return JSONResponse({"activity": []})


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------


@router.get("/statuses")
async def list_statuses(request: Request) -> JSONResponse:
    """List all status definitions grouped by category."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"statuses": []})

    db = getattr(ontology, "_work_adapter", None)
    if not db or not hasattr(db, "conn"):
        return JSONResponse({"statuses": []})

    try:
        cursor = db.conn.execute(
            "SELECT id, name, category, color, position, is_default FROM statuses ORDER BY position ASC"
        )
        statuses = [
            {"id": r[0], "name": r[1], "category": r[2], "color": r[3], "position": r[4], "is_default": bool(r[5])}
            for r in cursor.fetchall()
        ]
        return JSONResponse({"statuses": statuses})
    except Exception as e:
        logger.error(f"Failed to list statuses: {e}")
        return JSONResponse({"statuses": []})
