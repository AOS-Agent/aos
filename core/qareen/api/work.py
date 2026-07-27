"""Qareen API — Work routes.

Task, project, goal, and inbox management endpoints.
Ported from the legacy dashboard into typed FastAPI routes.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path as FilePath
from typing import Any

from fastapi import APIRouter, Path, Request, status
from fastapi.responses import JSONResponse

from ..events.types import Event
from ..ontology.types import ObjectType, TaskPriority, TaskStatus
from .schemas import (
    CreateGoalRequest,
    CreateInboxRequest,
    CreateProjectRequest,
    CreateTaskRequest,
    DelegateRequest,
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


def _work_adapter(ontology):
    """Resolve the WorkAdapter backing tasks.

    The activity/comments/statuses endpoints referenced ``ontology._work_adapter``
    (never set) and ``db.conn`` (the adapter exposes ``_conn``), so they
    silently returned empty regardless of data. Resolve the real adapter from
    the type registry.
    """
    adapters = getattr(ontology, "_adapters", None)
    if not adapters:
        return None
    return adapters.get(ObjectType.TASK)


def _work_conn(ontology):
    """Return the live sqlite connection for the work DB, or None."""
    adapter = _work_adapter(ontology)
    return getattr(adapter, "_conn", None) if adapter is not None else None


def _live_task_id() -> str | None:
    """Task id held by an active session right now, from the live-context file."""
    import json
    from pathlib import Path
    ctx = Path.home() / ".aos" / "work" / ".live-context.json"
    try:
        if ctx.exists():
            data = json.loads(ctx.read_text())
            return data.get("task_id")
    except Exception:
        pass
    return None


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
        stage=getattr(task, "stage", None),
        recurrence=task.recurrence,
        delegate=getattr(task, "delegate", None),
        held_by=getattr(task, "held_by", None),
        fields=getattr(task, "fields", None) or {},
        updated=getattr(task, "updated", None),
        live=getattr(task, "live", False),
        activity_count=getattr(task, "activity_count", 0) or 0,
        last_activity=getattr(task, "last_activity", None),
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
# Project briefs — compile, cache, and push
#
# The brief compiler lives in the work engine (core/engine/work/brief.py) and
# is imported lazily: this API must boot and serve every other route even when
# the compiler is absent, in which case the brief routes answer 503 rather than
# blowing up with a 500.
#
# Recompiles are debounced per project (BRIEF_DEBOUNCE_SECONDS) and always run
# off the request path — a task mutation never waits on, or fails because of,
# a brief compile.
# ---------------------------------------------------------------------------


BRIEF_DEBOUNCE_SECONDS = 2.0

_WORK_ENGINE_DIR = FilePath(__file__).resolve().parents[2] / "engine" / "work"

# project_id -> the sleeping task that will compile it when its window closes.
_brief_pending: dict[str, asyncio.Task] = {}
# Strong refs so the loop never garbage-collects an in-flight recompile.
_brief_tasks: set[asyncio.Task] = set()

_brief_module: Any = None


@dataclass(frozen=True)
class ProjectBriefUpdated(Event):
    """Emitted on /api/stream whenever a project brief is recompiled.

    Wire shape (sse._serialize_event flattens dataclass fields):

        event: project.brief.updated
        data: {"project_id": "hre", "state": "moving", "compiled_at": "..."}
    """

    event_type: str = "project.brief.updated"
    project_id: str = ""
    state: str = ""
    compiled_at: str | None = None


def _brief_engine() -> Any:
    """Import core/engine/work/brief.py, or return None if it isn't there yet.

    Only success is cached — the compiler is a parallel workstream, so a failed
    import must not poison the route for the life of the process.
    """
    global _brief_module
    if _brief_module is not None:
        return _brief_module

    if str(_WORK_ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(_WORK_ENGINE_DIR))
    try:
        import brief as brief_mod  # type: ignore[import-not-found]
    except Exception as e:
        logger.debug("brief compiler unavailable: %s", e)
        return None
    _brief_module = brief_mod
    return brief_mod


def _brief_unavailable() -> JSONResponse:
    """The one response shape for "the compiler isn't installed yet"."""
    return JSONResponse(
        {
            "error": "brief_compiler_unavailable",
            "detail": (
                "The project brief compiler (core/engine/work/brief.py) could "
                "not be imported. Briefs are unavailable until it is installed."
            ),
        },
        status_code=503,
    )


def _resolve_actor(explicit: str | None = None) -> Any:
    """Actor for a narrative write, via the attribution workstream's resolver.

    Falls back to a plain operator Actor when actor.py is absent but the
    dataclasses are, and to None when neither is importable.
    """
    if str(_WORK_ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(_WORK_ENGINE_DIR))
    try:
        from actor import resolve_actor  # type: ignore[import-not-found]

        return resolve_actor(explicit)
    except Exception:
        pass
    try:
        from brief_types import Actor  # type: ignore[import-not-found]

        return Actor(
            kind="operator",
            name=explicit or "operator",
            session_id=None,
            at=datetime.now().isoformat(),
        )
    except Exception:
        return None


def _project_of_task(ontology, task_id: str) -> str | None:
    """Which project a task belongs to — record first, id prefix as fallback."""
    try:
        task = ontology.get(ObjectType.TASK, task_id)
        if task is not None and getattr(task, "project", None):
            return task.project
    except Exception:
        pass
    # Project-scoped ids look like "aos#42" / "aos#42.1"; "t#1" is unassigned.
    if "#" in task_id:
        prefix = task_id.split("#", 1)[0].strip()
        if prefix and prefix != "t":
            return prefix
    return None


def _compile_brief_sync(project_id: str) -> dict[str, Any] | None:
    """Compile one brief (blocking — call via to_thread) as a dict. Never raises."""
    engine = _brief_engine()
    if engine is None:
        return None
    try:
        brief = engine.compile_brief(project_id)
        return engine.brief_to_dict(brief)
    except Exception:
        logger.exception("brief compile failed for %s", project_id)
        return None


async def _emit_brief_updated(bus, data: dict[str, Any]) -> None:
    """Push project.brief.updated onto the EventBus; SSE clients are already on it."""
    if bus is None or not data:
        return
    project_id = str(data.get("id") or data.get("project_id") or "")
    state = str(data.get("state") or "")
    compiled_at = data.get("compiled_at")
    try:
        await bus.emit(
            ProjectBriefUpdated(
                source="work-api",
                project_id=project_id,
                state=state,
                compiled_at=compiled_at,
                payload={
                    "project_id": project_id,
                    "state": state,
                    "compiled_at": compiled_at,
                },
            )
        )
    except Exception:
        logger.debug("bus emit failed for project.brief.updated (%s)", project_id)


async def _recompile_after(project_id: str, bus, delay: float) -> None:
    """Wait out the debounce window, then compile and push. Never raises."""
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        # The window closes the moment compilation starts: a mutation arriving
        # from here on opens a fresh window instead of being swallowed.
        _brief_pending.pop(project_id, None)
        data = await asyncio.to_thread(_compile_brief_sync, project_id)
        if data:
            await _emit_brief_updated(bus, data)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("brief recompile task failed for %s", project_id)
    finally:
        _brief_pending.pop(project_id, None)


def schedule_brief_recompile(
    request: Request,
    project_id: str | None,
    *,
    delay: float = BRIEF_DEBOUNCE_SECONDS,
) -> None:
    """Queue a debounced recompile of one project's brief. Fire-and-forget.

    Coalescing is fixed-window, not sliding: the first mutation opens a
    ``delay``-second window and every mutation landing inside it is absorbed by
    the same pending compile. A busy project therefore still recompiles every
    ``delay`` seconds instead of being starved by a perpetually reset timer.

    Safe to call from any mutation handler — it never raises and never blocks.
    """
    if not project_id:
        return
    try:
        bus = getattr(request.app.state, "bus", None)
        if bus is None:
            return
        pending = _brief_pending.get(project_id)
        if pending is not None and not pending.done():
            return  # already inside an open window
        task = asyncio.create_task(_recompile_after(project_id, bus, delay))
        _brief_pending[project_id] = task
        _brief_tasks.add(task)
        task.add_done_callback(_brief_tasks.discard)
    except Exception:
        logger.debug("could not schedule brief recompile for %s", project_id)


def schedule_brief_recompile_for_task(request: Request, ontology, task_id: str) -> None:
    """Same, resolving the project from the task the mutation touched."""
    try:
        schedule_brief_recompile(request, _project_of_task(ontology, task_id))
    except Exception:
        logger.debug("could not schedule brief recompile for task %s", task_id)


# ---------------------------------------------------------------------------
# Full work state
# ---------------------------------------------------------------------------


@router.get("/work", response_model=WorkResponse)
async def get_work(request: Request) -> WorkResponse:
    """Return full work state: tasks, projects, goals, inbox."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return WorkResponse()

    work_db = _work_adapter(ontology)

    # Authoritative counts — computed over the WHOLE table, not the returned
    # sample. The old code counted by_status over a 200-row created_at window,
    # which excluded the (older) active tasks and reported "0 active" while work
    # was in flight (spec §4 Phase 0 "honest counts").
    summary: dict[str, Any] = {}
    if work_db is not None and hasattr(work_db, "summary"):
        try:
            summary = work_db.summary()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("summary() failed: %s", e)

    # The live task: whatever session-linked task is being held right now.
    live_task_id = _live_task_id()

    # Tasks — the honest working set (all open + a bounded tail of closed),
    # never a truncated newest-created window.
    if work_db is not None and hasattr(work_db, "board_tasks"):
        tasks = work_db.board_tasks()
    else:
        tasks = ontology.list(ObjectType.TASK, limit=500)
    for t in tasks:
        if live_task_id and t.id == live_task_id:
            t.live = True  # type: ignore[attr-defined]
    task_responses = [_task_to_response(t) for t in tasks]

    # by_status/by_project come from the authoritative summary when available,
    # falling back to the returned set only if summary is unavailable.
    by_status: dict[str, int] = dict(summary.get("by_status", {}))
    by_project: dict[str, int] = {}
    for t in tasks:
        if t.project:
            by_project[t.project] = by_project.get(t.project, 0) + 1
    if not by_status:
        for t in tasks:
            s = t.status.value if hasattr(t.status, "value") else str(t.status)
            by_status[s] = by_status.get(s, 0) + 1

    task_list = TaskListResponse(
        tasks=task_responses,
        total=summary.get("total_tasks", len(task_responses)),
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
    raw_inbox = ontology.list(ObjectType.TASK, filters={"_type": "inbox"}, limit=100)
    if raw_inbox:
        for item in raw_inbox:
            if isinstance(item, dict):
                inbox_items.append(InboxItemResponse(
                    id=item.get("id", ""),
                    content=item.get("text", item.get("content", "")),
                    created=item.get("captured") or item.get("captured_at"),
                    source=item.get("source") or "manual",
                    snoozed_until=item.get("snoozed_until"),
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
        summary=summary,
        live_task_id=live_task_id,
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
    schedule_brief_recompile(
        request, body.project or (getattr(task, "project", None) if task else None)
    )
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
    schedule_brief_recompile_for_task(request, ontology, task_id)
    if task:
        return _task_to_response(task)
    return JSONResponse({"error": "Task not found after update"}, status_code=404)


@router.post("/tasks/{task_id}/delegate", response_model=TaskResponse)
async def delegate_task(
    body: DelegateRequest,
    request: Request,
    task_id: str = Path(..., description="Task ID to delegate"),
) -> TaskResponse | JSONResponse:
    """Delegate a task to an agent (the state transition, spec §3.1).

    Emits task.delegated — the runner's future pickup hook. No runner yet
    (Phase 4-5); this records the holder + state change + event.
    """
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute(
        "delegate_task",
        {"ontology": ontology, "task_id": task_id, "agent": body.agent},
        actor="operator",
    )
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)
    task = ontology.get(ObjectType.TASK, task_id)
    schedule_brief_recompile_for_task(request, ontology, task_id)
    if task:
        return _task_to_response(task)
    return JSONResponse({"error": "Task not found after delegate"}, status_code=404)


@router.post("/tasks/{task_id}/hold", response_model=TaskResponse)
async def hold_task(
    request: Request,
    task_id: str = Path(..., description="Task ID to take back"),
) -> TaskResponse | JSONResponse:
    """Take a delegated task back — operator becomes the holder again."""
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    result = await registry.execute(
        "hold_task",
        {"ontology": ontology, "task_id": task_id},
        actor="operator",
    )
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)
    task = ontology.get(ObjectType.TASK, task_id)
    schedule_brief_recompile_for_task(request, ontology, task_id)
    if task:
        return _task_to_response(task)
    return JSONResponse({"error": "Task not found after hold"}, status_code=404)


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

    # Resolve the owning project while the task still exists.
    project_id = _project_of_task(ontology, task_id)

    result = await registry.execute("delete_task", {
        "ontology": ontology,
        "task_id": task_id,
    }, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    schedule_brief_recompile(request, project_id)


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
    schedule_brief_recompile(
        request,
        body.project
        or (getattr(task, "project", None) if task else None)
        or _project_of_task(ontology, task_id),
    )
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

    schedule_brief_recompile_for_task(request, ontology, task_id)

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
    schedule_brief_recompile(request, project_id)
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


@router.post("/inbox/{inbox_id}/promote", status_code=status.HTTP_201_CREATED)
async def promote_inbox_item(
    request: Request,
    inbox_id: str = Path(..., description="Inbox item ID to promote to a task"),
) -> JSONResponse:
    """Promote an inbox item into a real task, then remove the inbox row.

    Body (optional): {title, project, priority}. Deleting the inbox row is the
    triage decision — ambient proposals keep their comms.db stamp so they are
    never re-proposed (proposer.py).
    """
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    conn = _work_conn(ontology)
    if conn is None:
        return JSONResponse({"error": "Database not available"}, status_code=503)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    row = conn.execute("SELECT text FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Inbox item not found: {inbox_id}"}, status_code=404)

    title = (body.get("title") or row[0] or "").strip()
    result = await registry.execute("create_task", {
        "ontology": ontology,
        "title": title,
        "project": body.get("project"),
        "priority": int(body.get("priority", 3)),
    }, actor="operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)

    conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    conn.commit()
    schedule_brief_recompile(request, body.get("project"))
    return JSONResponse({"task_id": result["result"]["task_id"], "promoted": inbox_id}, status_code=201)


@router.post("/inbox/{inbox_id}/snooze")
async def snooze_inbox_item(
    request: Request,
    inbox_id: str = Path(..., description="Inbox item ID to snooze"),
) -> JSONResponse:
    """Defer an inbox item until a timestamp. Body: {until: ISO8601}."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    conn = _work_conn(ontology)
    if conn is None:
        return JSONResponse({"error": "Database not available"}, status_code=503)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    until = body.get("until")
    if not until:
        # Default: snooze one day.
        from datetime import datetime, timedelta
        until = (datetime.now() + timedelta(days=1)).isoformat()

    cur = conn.execute(
        "UPDATE inbox SET snoozed_until = ? WHERE id = ?", (until, inbox_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        return JSONResponse({"error": f"Inbox item not found: {inbox_id}"}, status_code=404)
    return JSONResponse({"snoozed": inbox_id, "until": until})


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
    conn = _work_conn(ontology)
    if conn is None:
        return JSONResponse({"comments": []})

    try:
        cursor = conn.execute(
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

    conn = _work_conn(ontology)
    if conn is None:
        return JSONResponse({"error": "Database not available"}, status_code=503)

    import uuid
    from datetime import datetime
    comment_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    try:
        conn.execute(
            "INSERT INTO comments (id, entity_type, entity_id, author_id, author_type, body, created_at) "
            "VALUES (?, 'task', ?, ?, ?, ?, ?)",
            (comment_id, task_id, author_id, author_type, comment_body, now),
        )
        conn.commit()
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


@router.post("/tasks/{task_id}/activity", status_code=status.HTTP_201_CREATED)
async def append_activity_endpoint(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> JSONResponse:
    """Append a narrative activity entry (attempt / proof / comment / …).

    The agent/operator hand-append path. Emits task.activity for SSE liveness.
    Auto-narration kinds (created/status_changed/…) are refused — the system
    writes those on every mutation.
    """
    registry = getattr(request.app.state, "action_registry", None)
    ontology = getattr(request.app.state, "ontology", None)
    if not registry or not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    body = await request.json()
    kind = (body.get("kind") or "").strip()
    text = (body.get("body") or "").strip()
    if not kind or not text:
        return JSONResponse({"error": "kind and body are required"}, status_code=400)

    result = await registry.execute("append_activity", {
        "ontology": ontology,
        "task_id": task_id,
        "kind": kind,
        "body": text,
        "data": body.get("data"),
        "actor": body.get("actor"),
    }, actor=body.get("actor") or "operator")
    if not result.get("success"):
        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)
    schedule_brief_recompile_for_task(request, ontology, task_id)
    return JSONResponse(result["result"], status_code=201)


@router.get("/tasks/{task_id}/activity")
async def get_activity(
    request: Request,
    task_id: str = Path(..., description="Task ID"),
) -> JSONResponse:
    """The task's NARRATIVE activity timeline (task_activity), oldest-first.

    This is the story the card tells — typed events with a human body and an
    expandable data payload (spec §3.3). Comments are folded in as their own
    kind. The forensic per-field log (entity_history) is deliberately NOT
    included here; it is the separate audit layer, not the narrative.
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"activity": []})

    adapter = _work_adapter(ontology)
    conn = _work_conn(ontology)
    if adapter is None or conn is None:
        return JSONResponse({"activity": []})

    try:
        activity = []
        if hasattr(adapter, "list_activity"):
            for a in adapter.list_activity(task_id):
                activity.append({
                    "type": "activity",
                    "id": a["id"],
                    "kind": a["kind"],
                    "body": a["body"],
                    "data": a.get("data") or {},
                    "actor": a["actor"],
                    "actor_type": a["actor_type"],
                    "timestamp": a["ts"],
                    "source_event_id": a.get("source_event_id"),
                })

        # Fold comments into the narrative as their own kind.
        try:
            cursor = conn.execute(
                "SELECT id, author_id, author_type, body, created_at "
                "FROM comments WHERE entity_type = 'task' AND entity_id = ? "
                "ORDER BY created_at ASC",
                (task_id,),
            )
            for row in cursor.fetchall():
                activity.append({
                    "type": "activity", "kind": "comment", "id": row[0],
                    "actor": row[1], "actor_type": row[2], "body": row[3],
                    "data": {}, "timestamp": row[4],
                })
        except Exception:
            pass

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

    conn = _work_conn(ontology)
    if conn is None:
        return JSONResponse({"statuses": []})

    try:
        # pipeline is NULL for the generic board columns, 'bug' for bug-pipeline
        # stages — the frontend renders generic columns and reads the bug stage
        # set to label bug cards. Column-guarded for a pre-Phase-1 statuses table.
        has_pipeline = any(
            r[1] == "pipeline" for r in conn.execute("PRAGMA table_info(statuses)")
        )
        pcol = "pipeline" if has_pipeline else "NULL AS pipeline"
        cursor = conn.execute(
            f"SELECT id, name, category, color, position, is_default, {pcol} "
            "FROM statuses ORDER BY position ASC"
        )
        statuses = [
            {"id": r[0], "name": r[1], "category": r[2], "color": r[3],
             "position": r[4], "is_default": bool(r[5]), "pipeline": r[6]}
            for r in cursor.fetchall()
        ]
        return JSONResponse({"statuses": statuses})
    except Exception as e:
        logger.error(f"Failed to list statuses: {e}")
        return JSONResponse({"statuses": []})


# ---------------------------------------------------------------------------
# Project brief routes
# ---------------------------------------------------------------------------


@router.get("/work/projects/{project_id}/brief")
async def get_project_brief(
    request: Request,
    project_id: str = Path(..., description="Project ID, e.g. hre"),
    refresh: bool = False,
) -> JSONResponse:
    """The compiled ProjectBrief.

    Serves the cached brief when there is one; compiles on a cache miss (or
    ``?refresh=true``) so the first ever request still returns a real brief.
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    engine = _brief_engine()
    if engine is None:
        return _brief_unavailable()

    project = ontology.get(ObjectType.PROJECT, project_id)
    if not project:
        return JSONResponse({"error": f"Project not found: {project_id}"}, status_code=404)

    def _load() -> dict[str, Any] | None:
        brief = None
        if not refresh:
            # load_or_refresh, not load_brief: recompile-on-mutation only fires
            # for changes made THROUGH this API. Work done from the CLI, by an
            # agent, or by a hook writes straight to the DB, so a cached brief
            # could be hours stale while the page claimed "last activity just
            # now". Pulling on read makes freshness independent of who wrote.
            loader = getattr(engine, "load_or_refresh", None)
            brief = loader(project_id) if loader else engine.load_brief(project_id)
        if brief is None:
            brief = engine.compile_brief(project_id)
        return engine.brief_to_dict(brief) if brief is not None else None

    try:
        data = await asyncio.to_thread(_load)
    except Exception as e:
        logger.exception("brief compile failed for %s", project_id)
        return JSONResponse(
            {"error": "brief_compile_failed", "detail": str(e)}, status_code=500
        )

    if data is None:
        return JSONResponse({"error": f"No brief for project: {project_id}"}, status_code=404)
    return JSONResponse(data)


@router.post("/work/projects/{project_id}/brief/narrative")
async def write_project_narrative(
    request: Request,
    project_id: str = Path(..., description="Project ID, e.g. hre"),
) -> JSONResponse:
    """Write the agent-authored narrative paragraph onto a project's brief.

    Body: ``{"text": "...", "actor": "chief"}`` — ``actor`` optional, resolved
    by the work engine when omitted. Pushes project.brief.updated so any open
    brief page picks the narrative up immediately.
    """
    engine = _brief_engine()
    if engine is None:
        return _brief_unavailable()

    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    actor = _resolve_actor(body.get("actor"))
    if actor is None:
        # No actor.py and no brief_types.Actor — writing an unsigned narrative
        # would violate the contract's "every change is signed" rule.
        return JSONResponse(
            {
                "error": "actor_unavailable",
                "detail": (
                    "Could not resolve an Actor (core/engine/work/actor.py). "
                    "A narrative is never written unattributed."
                ),
            },
            status_code=503,
        )

    try:
        await asyncio.to_thread(engine.set_narrative, project_id, text, actor)
    except Exception as e:
        logger.exception("set_narrative failed for %s", project_id)
        return JSONResponse(
            {"error": "narrative_write_failed", "detail": str(e)}, status_code=500
        )

    # Recompile now (no debounce) so the brief's narrative_* stamps go live.
    schedule_brief_recompile(request, project_id, delay=0)

    return JSONResponse({
        "ok": True,
        "project_id": project_id,
        "narrative": text,
        "actor": getattr(actor, "name", None),
    })


@router.post("/work/projects/{project_id}/brief/recompile")
async def recompile_project_brief(
    request: Request,
    project_id: str = Path(..., description="Project ID, e.g. hre"),
) -> JSONResponse:
    """Force a recompile, bypassing the debounce, and return the fresh brief."""
    engine = _brief_engine()
    if engine is None:
        return _brief_unavailable()

    try:
        brief = await asyncio.to_thread(engine.compile_brief, project_id)
        data = await asyncio.to_thread(engine.brief_to_dict, brief)
    except Exception as e:
        logger.exception("forced recompile failed for %s", project_id)
        return JSONResponse(
            {"error": "brief_compile_failed", "detail": str(e)}, status_code=500
        )

    await _emit_brief_updated(getattr(request.app.state, "bus", None), data)
    return JSONResponse(data)
