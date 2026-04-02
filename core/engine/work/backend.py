"""
AOS Work Backend — Ontology-backed work engine for CLI use.

Replaces the old engine.py with ontology-based access.
Provides the same function signatures so cli.py needs minimal changes.

All functions return plain dicts (not dataclasses) because query.py and
cli.py expect dict access via .get(). The _to_dict() conversion is the
bridge between the ontology's typed objects and the CLI's dict world.

Side effects (activity log, dashboard SSE, GitHub sync, initiative
checkbox sync) are handled here in sync form — no async, no event bus.
"""

import dataclasses
import fcntl
import json
import logging
import os
import re
import subprocess

# ── Path setup for ontology imports ─────────────────────
import sys
import tempfile
import urllib.request
from datetime import date, datetime
from pathlib import Path

import yaml

# Walk up from __file__ (without resolving symlinks) to find the project root.
# backend.py lives at <root>/core/work/backend.py — go up 3 levels.
_this_dir = Path(__file__).parent                     # core/work/
_project_root = _this_dir.parent.parent               # <root>/
sys.path.insert(0, str(_project_root))

# If symlinks resolved differently (e.g., core/engine/work/), also try
# finding root by walking up until we find core/qareen/.
if not (_project_root / "core" / "qareen").is_dir():
    _candidate = Path(__file__).resolve().parent
    for _ in range(6):
        _candidate = _candidate.parent
        if (_candidate / "core" / "qareen").is_dir():
            _project_root = _candidate
            sys.path.insert(0, str(_project_root))
            break

from core.qareen.ontology.adapters.work import WorkAdapter
from core.qareen.ontology.types import (
    Goal,
    Project,
    Task,
    TaskPriority,
    TaskStatus,
)
from core.qareen.ontology.work_utils import (
    HandoffFormatter,
    LiveContext,
    ProjectContext,
    TaskResolver,
)

_gh_log = logging.getLogger("work.github")


# ── Constants ───────────────────────────────────────────

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
WORK_DIR = Path.home() / ".aos" / "work"
ACTIVITY_FILE = WORK_DIR / "activity.yaml"
DASHBOARD_URL = "http://127.0.0.1:4096"
AOS_REPO = "hishamalhadi/aos"
MAX_ACTIVITY = 100


# ── Lazy singletons ────────────────────────────────────

_adapter: WorkAdapter | None = None
_resolver: TaskResolver | None = None
_project_ctx: ProjectContext | None = None
_live_ctx = LiveContext()
_handoff_fmt = HandoffFormatter()


def _get_adapter() -> WorkAdapter:
    """Get or create the WorkAdapter singleton."""
    global _adapter
    if _adapter is None:
        db = str(DB_PATH)
        if not DB_PATH.exists():
            # Create parent directories so SQLite can create the file
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _adapter = WorkAdapter(db_path=db)
    return _adapter


def _get_resolver() -> TaskResolver:
    global _resolver
    if _resolver is None:
        _resolver = TaskResolver(_get_adapter())
    return _resolver


def _get_project_ctx() -> ProjectContext:
    global _project_ctx
    if _project_ctx is None:
        _project_ctx = ProjectContext(_get_adapter())
    return _project_ctx


# ── Dict conversion ─────────────────────────────────────

def _to_dict(obj):
    """Convert a dataclass or dict to a plain dict for query.py compatibility.

    This is the critical bridge function. The CLI and query.py expect
    dicts with .get() access and specific key names that match the
    YAML-era format (e.g., 'project' not 'project_id', 'created' not
    'created_at'). The ontology returns typed dataclasses.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if not dataclasses.is_dataclass(obj):
        return obj

    # ── Task-specific conversion (matches engine._row_to_task format) ──
    if isinstance(obj, Task):
        return _task_to_dict(obj)

    # ── Project-specific conversion ──
    if isinstance(obj, Project):
        return _project_to_dict(obj)

    # ── Goal-specific conversion ──
    if isinstance(obj, Goal):
        return _goal_to_dict(obj)

    # ── Generic fallback for other dataclasses ──
    d = dataclasses.asdict(obj)
    return d


def _task_to_dict(task: Task) -> dict:
    """Convert a Task dataclass to the dict format cli.py/query.py expect.

    This matches the exact key names from engine._row_to_task.
    """
    d = {
        "id": task.id,
        "title": task.title,
        "status": task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
        "priority": task.priority.value if isinstance(task.priority, TaskPriority) else int(task.priority),
        "created": task.created.isoformat() if task.created else "",
        "source": task.created_by or "manual",
    }

    if task.project:
        d["project"] = task.project
    if task.started:
        d["started"] = task.started.isoformat()
    if task.completed:
        d["completed"] = task.completed.isoformat()
    if task.due:
        d["due"] = task.due.isoformat()
    if task.parent_id:
        d["parent"] = task.parent_id
    if task.tags:
        d["tags"] = task.tags
    if task.description:
        d["notes"] = task.description
    if task.assigned_to:
        d["assigned_to"] = task.assigned_to
    if task.pipeline:
        d["pipeline"] = task.pipeline
    if task.recurrence:
        d["recurrence"] = task.recurrence

    # Extract energy/context/source_ref from description metadata comment
    if task.description:
        meta_match = re.search(r'<!-- meta: (.+?) -->', task.description)
        if meta_match:
            for part in meta_match.group(1).split(", "):
                if ":" in part:
                    k, v = part.split(":", 1)
                    if k in ("energy", "context", "source_ref"):
                        d[k] = v

    # Handoff
    if task.handoff:
        handoff = {
            "updated": task.handoff.timestamp.isoformat() if task.handoff.timestamp else "",
            "state": task.handoff.state or "",
        }
        if task.handoff.next_step:
            handoff["next_step"] = task.handoff.next_step
        if task.handoff.files:
            handoff["files_touched"] = task.handoff.files
        if task.handoff.decisions:
            handoff["decisions"] = task.handoff.decisions
        if task.handoff.blockers:
            handoff["blockers"] = task.handoff.blockers
        d["handoff"] = handoff

    # Dynamically-attached attributes from the adapter
    if hasattr(task, "sessions") and getattr(task, "sessions", None):
        d["sessions"] = task.sessions
    if hasattr(task, "subtasks") and getattr(task, "subtasks", None):
        d["subtasks"] = [_task_to_dict(st) if isinstance(st, Task) else _to_dict(st) for st in task.subtasks]
    if hasattr(task, "auto_completed") and getattr(task, "auto_completed", False):
        d["auto_completed"] = True

    return d


def _project_to_dict(project: Project) -> dict:
    """Convert a Project dataclass to the dict format cli.py expects."""
    d = {
        "id": project.id,
        "title": project.title,
        "status": project.status or "active",
    }
    if project.description:
        d["description"] = project.description
    if project.path:
        d["path"] = project.path
    if project.goal:
        d["goal"] = project.goal
    if project.done_when:
        d["done_when"] = project.done_when
    if project.stages:
        d["stages"] = project.stages
    if project.current_stage:
        d["current_stage"] = project.current_stage
    if project.telegram_bot_key:
        d["telegram_bot_key"] = project.telegram_bot_key
    if project.telegram_chat_key:
        d["telegram_chat_key"] = project.telegram_chat_key
    if project.telegram_forum_topic:
        d["telegram_forum_topic"] = project.telegram_forum_topic
    # Computed counts
    d["task_count"] = project.task_count
    d["done_count"] = project.done_count
    d["active_count"] = project.active_count
    return d


def _goal_to_dict(goal: Goal) -> dict:
    """Convert a Goal dataclass to the dict format cli.py expects."""
    d = {
        "id": goal.id,
        "title": goal.title,
        "status": "active",
    }
    if goal.weight:
        d["weight"] = goal.weight
    if goal.description:
        d["description"] = goal.description
    if goal.project:
        d["project"] = goal.project
    if goal.key_results:
        d["key_results"] = [
            {
                "title": kr.title,
                "progress": kr.progress,
                "target": kr.target,
            }
            for kr in goal.key_results
        ]
    return d


# ── Side effects (sync) ────────────────────────────────

def _log_activity(action: str, task_id: str = None, title: str = None,
                  project: str = None, detail: str = None) -> None:
    """Append an event to the activity log. Fire-and-forget."""
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        event = {"ts": _now(), "action": action}
        if task_id:
            event["task_id"] = task_id
        if title:
            event["title"] = title
        if project:
            event["project"] = project
        if detail:
            event["detail"] = detail

        activity_lock = WORK_DIR / ".activity.lock"
        with open(activity_lock, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                events = []
                if ACTIVITY_FILE.exists():
                    with open(ACTIVITY_FILE, "r") as f:
                        events = yaml.safe_load(f) or []
                events.insert(0, event)
                events = events[:MAX_ACTIVITY]
                fd, tmp = tempfile.mkstemp(dir=str(WORK_DIR), suffix=".activity.tmp")
                try:
                    with os.fdopen(fd, "w") as f:
                        yaml.dump(events, f, default_flow_style=False,
                                  sort_keys=False, allow_unicode=True)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(ACTIVITY_FILE))
                except Exception:
                    os.unlink(tmp)
                    raise
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        _notify_dashboard(event)
    except Exception:
        pass  # Activity log is best-effort


def _notify_dashboard(event: dict) -> None:
    """POST work event to dashboard for instant SSE push. Best-effort."""
    try:
        data = json.dumps(event).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/work/notify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # Dashboard may not be running


def _gh_create_issue(task_id: str, title: str, priority: int = 3,
                     subtask_titles: list[str] | None = None) -> str | None:
    """Create a GitHub Issue. Returns the issue URL or None on failure."""
    try:
        labels = f"task,P{priority}"
        body = f"**Task ID:** `{task_id}`"
        if subtask_titles:
            labels += ",initiative"
            body += "\n\n## Subtasks\n\n"
            body += "\n".join(f"- [ ] {st}" for st in subtask_titles)

        result = subprocess.run(
            ["gh", "issue", "create",
             "--repo", AOS_REPO,
             "--title", f"[{task_id}] {title}",
             "--body", body,
             "--label", labels],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            _gh_log.info(f"Created issue: {url}")
            return url
        _gh_log.warning(f"gh issue create failed: {result.stderr[:200]}")
    except Exception as e:
        _gh_log.debug(f"GitHub sync skipped: {e}")
    return None


def _gh_close_issue(task_id: str) -> bool:
    """Close a GitHub Issue by searching for its task ID in the title."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list",
             "--repo", AOS_REPO,
             "--search", f"[{task_id}] in:title",
             "--state", "open",
             "--json", "number",
             "--limit", "1"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False

        issues = json.loads(result.stdout)
        if not issues:
            return False

        issue_num = issues[0]["number"]
        close_result = subprocess.run(
            ["gh", "issue", "close",
             "--repo", AOS_REPO,
             str(issue_num)],
            capture_output=True, text=True, timeout=15,
        )
        if close_result.returncode == 0:
            _gh_log.info(f"Closed issue #{issue_num} for {task_id}")
            return True
    except Exception as e:
        _gh_log.debug(f"GitHub close skipped: {e}")
    return False


def _sync_initiative_checkbox(task: dict) -> None:
    """If task has source_ref to an initiative doc, check off matching checkbox."""
    source_ref = task.get("source_ref")
    if not source_ref:
        return
    try:
        doc_path = Path.home() / source_ref.lstrip("~/")
        if not doc_path.exists():
            doc_path = Path.home() / source_ref
        if not doc_path.exists():
            return

        content = doc_path.read_text()
        title = task.get("title", "")
        task_id = task.get("id", "")

        updated = False
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if not re.match(r'\s*- \[ \]', line):
                continue
            if task_id and task_id in line:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                updated = True
                break
            if title and title[:40].lower() in line.lower():
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                updated = True
                break

        if updated:
            today = datetime.now().strftime("%Y-%m-%d")
            new_content = "\n".join(lines)
            new_content = re.sub(
                r'^updated:.*$', f'updated: {today}',
                new_content, count=1, flags=re.MULTILINE
            )
            tmp = str(doc_path) + ".tmp"
            Path(tmp).write_text(new_content)
            os.replace(tmp, str(doc_path))

            notify_initiative_event(
                "initiative_update",
                title=title,
                detail=f"Checkbox synced: {task_id}",
                task_id=task_id,
            )
    except Exception:
        pass  # Best-effort


def _on_task_created(task_dict: dict) -> None:
    """Side effects after task creation."""
    _log_activity("task_created", task_dict.get("id"), task_dict.get("title"),
                  task_dict.get("project"))
    _notify_dashboard({
        "action": "task_created",
        "task_id": task_dict.get("id"),
        "title": task_dict.get("title"),
        "ts": datetime.now().isoformat(),
    })
    if task_dict.get("project") == "aos":
        _gh_create_issue(task_dict.get("id"), task_dict.get("title"),
                         task_dict.get("priority", 3))


def _on_task_completed(task_dict: dict) -> None:
    """Side effects after task completion."""
    _log_activity("task_completed", task_dict.get("id"), task_dict.get("title"),
                  task_dict.get("project"))
    _notify_dashboard({
        "action": "task_completed",
        "task_id": task_dict.get("id"),
        "title": task_dict.get("title"),
        "ts": datetime.now().isoformat(),
    })
    if task_dict.get("project") == "aos":
        _gh_close_issue(task_dict.get("id"))
    _sync_initiative_checkbox(task_dict)


# ── Utility ─────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# ── Public API (matches engine.py function signatures) ──


# ── Context Detection ───────────────────────────────────

def detect_project_from_cwd(cwd: str = None) -> str | None:
    """Detect project from current working directory."""
    return _get_project_ctx().detect_from_cwd(cwd)


# ── Resolution ──────────────────────────────────────────

def resolve_task(query_str: str, tasks: list = None) -> dict | None:
    """Resolve a task by exact ID, partial ID, or fuzzy title match."""
    if tasks is not None:
        # If tasks are provided, use the old-style resolution directly
        # (query.py compatibility path)
        return _get_resolver().resolve(query_str)
    return _to_dict(_get_resolver().resolve(query_str))


def resolve_task_in_project(query_str: str, project_id: str = None) -> dict | None:
    """Resolve with project context."""
    return _to_dict(_get_resolver().resolve(query_str, project_id))


# ── Task CRUD ───────────────────────────────────────────

def get_all_tasks() -> list:
    """Get all tasks as dicts."""
    tasks = _get_adapter().list(limit=10000)
    return [_to_dict(t) for t in tasks]


def get_task(task_id: str) -> dict | None:
    """Get a single task by exact ID."""
    result = _get_adapter().get(task_id)
    if result is None:
        return None
    return _to_dict(result)


def add_task(title: str, priority: int = 3, project: str = None,
             status: str = "todo", tags: list = None, source: str = "manual",
             due: str = None, energy: str = None, context: str = None,
             parent: str = None, source_ref: str = None,
             notes: str = None) -> dict:
    """Add a new task with project-scoped ID."""
    task = Task(
        id="",  # auto-generated by adapter.create()
        title=title,
        status=TaskStatus(status) if status in [s.value for s in TaskStatus] else TaskStatus.TODO,
        priority=TaskPriority(priority) if priority in [p.value for p in TaskPriority] else TaskPriority.NORMAL,
        project=project,
        tags=tags or [],
        description=notes,
        created_by=source,
        due=None,  # Set below if provided
        parent_id=parent,
        energy=energy,
        context=context,
    )

    # Handle due date string
    if due:
        try:
            if len(due) == 10:
                task.due = datetime.fromisoformat(due + "T00:00:00")
            else:
                task.due = datetime.fromisoformat(due)
        except ValueError:
            pass

    # source_ref is encoded in description metadata by the adapter
    if source_ref:
        if not task.description:
            task.description = ""
        # The adapter handles meta encoding, but we need source_ref accessible
        # Store it as a metadata comment that _task_to_dict can extract
        meta_parts = []
        if energy:
            meta_parts.append(f"energy:{energy}")
        if context:
            meta_parts.append(f"context:{context}")
        meta_parts.append(f"source_ref:{source_ref}")
        meta_comment = "<!-- meta: " + ", ".join(meta_parts) + " -->"
        if notes:
            task.description = notes + "\n\n" + meta_comment
        else:
            task.description = meta_comment

    result = _get_adapter().create(task)
    result_dict = _to_dict(result)

    # Fire side effects
    if parent:
        _log_activity("subtask_added", result_dict.get("id"), title, project,
                      detail=f"under {parent}")
    else:
        _on_task_created(result_dict)

    return result_dict


def add_subtask(parent_id: str, title: str, priority: int = None,
                status: str = "todo") -> dict | None:
    """Add a subtask to an existing task."""
    result = _get_adapter().add_subtask(parent_id, title, priority=priority, status=status)
    if result is None:
        return None
    result_dict = _to_dict(result)
    _log_activity("subtask_added", result_dict.get("id"), title,
                  result_dict.get("project"), detail=f"under {parent_id}")
    return result_dict


def complete_task(task_id: str) -> dict | None:
    """Mark a task as done. Auto-cascades parent if all siblings done."""
    result = _get_adapter().complete_task(task_id)
    if result is None:
        return None

    result_dict = _to_dict(result)

    # Side effects
    _on_task_completed(result_dict)

    # Clear live context if this was the active task
    clear_live_context(task_id)

    # If parent auto-completed via cascade, fire side effects for that too
    if result_dict.get("auto_completed"):
        parent_id = result_dict.get("parent")
        if parent_id:
            parent = get_task(parent_id)
            if parent and parent.get("status") == "done":
                notify_initiative_event(
                    "phase_completed",
                    parent.get("title", parent_id),
                    task_id=parent_id,
                    project=parent.get("project"),
                )
                _sync_initiative_checkbox(parent)

    return result_dict


def start_task(task_id: str, session_id: str = None) -> dict | None:
    """Move a task to active status and set live context."""
    result = _get_adapter().start_task(task_id)
    if result is None:
        return None

    result_dict = _to_dict(result)
    _log_activity("task_started", result_dict.get("id"),
                  result_dict.get("title"), result_dict.get("project"))
    set_live_context(result_dict, session_id=session_id)
    return result_dict


def cancel_task(task_id: str) -> dict | None:
    """Cancel a task."""
    result = _get_adapter().cancel_task(task_id)
    if result is None:
        return None
    result_dict = _to_dict(result)
    _log_activity("task_cancelled", result_dict.get("id"),
                  result_dict.get("title"), result_dict.get("project"))
    clear_live_context(task_id)
    return result_dict


def update_task(task_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a task."""
    result = _get_adapter().update(task_id, fields)
    if result is None:
        return None
    return _to_dict(result)


def delete_task(task_id: str) -> bool:
    """Permanently remove a task."""
    return _get_adapter().delete(task_id)


def get_subtasks(parent_id: str) -> list:
    """Get all subtasks of a parent task."""
    tasks = _get_adapter().list(filters={"parent_id": parent_id}, limit=10000)
    return [_to_dict(t) for t in tasks]


def get_task_tree(task_id: str) -> dict | None:
    """Get a task with its subtasks attached."""
    result = _get_adapter().get_task_tree(task_id)
    if result is None:
        return None
    return _to_dict(result)


# ── Handoff ─────────────────────────────────────────────

def write_handoff(task_id: str, state: str, next_step: str = None,
                  files_touched: list = None, decisions: list = None,
                  blockers: list = None) -> dict | None:
    """Write handoff context for a task."""
    result = _get_adapter().write_handoff(
        task_id, state,
        next_step=next_step,
        files_touched=files_touched,
        decisions=decisions,
        blockers=blockers,
    )
    if result is None:
        return None
    result_dict = _to_dict(result)
    _log_activity("handoff_written", result_dict.get("id"),
                  result_dict.get("title"), result_dict.get("project"),
                  detail=next_step[:80] if next_step else None)
    return result_dict


def get_handoff(task_id: str) -> dict | None:
    """Get handoff context for a task."""
    task = get_task(task_id)
    if task:
        return task.get("handoff")
    return None


def build_handoff_prompt(task_id: str) -> str | None:
    """Build a dispatch prompt section from a task's handoff context."""
    task = get_task(task_id)
    if not task:
        return None
    return _handoff_fmt.build_prompt(task)


# ── Live Context ────────────────────────────────────────

def get_live_context() -> dict | None:
    """Read current live context."""
    return _live_ctx.get()


def set_live_context(task: dict, session_id: str = None) -> None:
    """Set the live work context."""
    _live_ctx.set(task, session_id=session_id)


def clear_live_context(task_id: str = None) -> dict | None:
    """Clear live context. Returns old context if it existed."""
    return _live_ctx.clear(task_id)


# ── Project CRUD ────────────────────────────────────────

def get_all_projects() -> list:
    """Get all projects as dicts."""
    projects = _get_adapter().list(filters={"_type": "project"}, limit=10000)
    return [_to_dict(p) for p in projects]


def add_project(title: str, goal: str = None, done_when: str = None,
                appetite: str = None, short_id: str = None,
                initiative: str = None, project_id: str = None) -> dict:
    """Create a new project."""
    # Build description from extra fields not in schema
    desc_parts = []
    if appetite:
        desc_parts.append(f"appetite:{appetite}")
    if short_id:
        desc_parts.append(f"short_id:{short_id}")
    if initiative:
        desc_parts.append(f"initiative:{initiative}")
    description = ", ".join(desc_parts) if desc_parts else None

    project = Project(
        id=project_id or "",  # auto-generated by adapter if empty
        title=title,
        description=description,
        goal=goal,
        done_when=done_when,
    )
    result = _get_adapter().create(project)
    result_dict = _to_dict(result)

    # Enrich return dict with extra fields for CLI display
    if appetite:
        result_dict["appetite"] = appetite
    if short_id:
        result_dict["short_id"] = short_id
    if initiative:
        result_dict["initiative"] = initiative
    if not result_dict.get("started"):
        result_dict["started"] = _today()

    return result_dict


def update_project(project_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a project."""
    result = _get_adapter().update(project_id, fields)
    if result is None:
        return None
    return _to_dict(result)


def delete_project(project_id: str) -> bool:
    """Remove a project."""
    return _get_adapter().delete(project_id)


# ── Goal CRUD ───────────────────────────────────────────

def get_all_goals() -> list:
    """Get all goals as dicts."""
    goals = _get_adapter().list(filters={"_type": "goal"}, limit=10000)
    return [_to_dict(g) for g in goals]


def add_goal(title: str, goal_type: str = "committed", weight: float = None) -> dict:
    """Create a new goal."""
    goal = Goal(
        id="",  # auto-generated
        title=title,
        weight=int(weight) if weight is not None else 0,
        description=goal_type,
    )
    result = _get_adapter().create(goal)
    result_dict = _to_dict(result)
    result_dict["type"] = goal_type
    return result_dict


def update_goal(goal_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a goal."""
    result = _get_adapter().update(goal_id, fields)
    if result is None:
        return None
    return _to_dict(result)


def delete_goal(goal_id: str) -> bool:
    """Remove a goal."""
    return _get_adapter().delete(goal_id)


# ── Thread CRUD ─────────────────────────────────────────

def get_all_threads() -> list:
    """Get all threads."""
    threads = _get_adapter().list(filters={"_type": "thread"}, limit=10000)
    return [_to_dict(t) for t in threads]


def get_thread(thread_id: str) -> dict | None:
    """Get a single thread by ID."""
    result = _get_adapter().get(thread_id)
    if result is None:
        return None
    return _to_dict(result)


def add_thread(title: str, session_id: str = None) -> dict:
    """Create a new thread."""
    result = _get_adapter().create({
        "_type": "thread",
        "title": title,
        "session_id": session_id,
    })
    return _to_dict(result) if not isinstance(result, dict) else result


def update_thread(thread_id: str, **fields) -> dict | None:
    """Update fields on a thread."""
    # Threads use adapter.update but need the thread in the threads table
    adapter = _get_adapter()
    # Thread updates go through direct SQL since adapter.update checks tasks first
    conn = adapter._conn
    field_map = {
        "title": "title",
        "status": "status",
        "project": "project_id",
        "project_id": "project_id",
    }
    sets = []
    params = []
    for key, val in fields.items():
        col = field_map.get(key)
        if col:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return get_thread(thread_id)
    params.append(thread_id)
    conn.execute(f"UPDATE threads SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_thread(thread_id)


def promote_thread(thread_id: str, project_title: str = None,
                   goal: str = None) -> dict | None:
    """Promote a thread to a project."""
    result = _get_adapter().promote_thread(thread_id, project_title=project_title, goal=goal)
    if result is None:
        return None
    return _to_dict(result)


def find_thread_by_cwd(cwd: str) -> dict | None:
    """Find an active thread for a working directory. Always None (DB has no cwd column)."""
    return None


# ── Inbox ───────────────────────────────────────────────

def get_inbox() -> list:
    """Get all inbox items."""
    items = _get_adapter().list(filters={"_type": "inbox"}, limit=10000)
    return [_to_dict(i) for i in items]


def add_inbox(text: str, source: str = "manual", confidence: float = None) -> dict:
    """Add an inbox item."""
    obj = {"text": text, "source": source}
    if confidence is not None:
        obj["confidence"] = confidence
    result = _get_adapter().create(obj)
    return _to_dict(result) if not isinstance(result, dict) else result


def promote_inbox(inbox_id: str, as_title: str = None) -> dict | None:
    """Promote an inbox item to a task."""
    adapter = _get_adapter()
    row = adapter._conn.execute(
        "SELECT * FROM inbox WHERE id = ?", (inbox_id,)
    ).fetchone()
    if not row:
        return None
    title = as_title or row["text"]
    adapter._conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    adapter._conn.commit()
    return add_task(title, source="inbox")


def delete_inbox(inbox_id: str) -> bool:
    """Delete an inbox item."""
    adapter = _get_adapter()
    cur = adapter._conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    adapter._conn.commit()
    return cur.rowcount > 0


# ── Session Linking ─────────────────────────────────────

def link_session_to_task(task_id: str, session_id: str, outcome: str = None,
                         date_str: str = None) -> dict | None:
    """Link a session to a task."""
    result = _get_adapter().link_session_to_task(task_id, session_id, outcome=outcome)
    if result is None:
        return None
    return _to_dict(result)


def link_session_to_thread(thread_id: str, session_id: str,
                           notes: str = None, cwd: str = None,
                           project: str = None) -> dict | None:
    """Link a session to a thread."""
    result = _get_adapter().link_session_to_thread(
        thread_id, session_id, cwd=cwd, project=project
    )
    if result is None:
        return None
    d = _to_dict(result) if not isinstance(result, dict) else result
    # CLI expects d["sessions"] as a list for len() — adapter returns session_count
    if "session_count" in d and "sessions" not in d:
        d["sessions"] = ["s"] * d["session_count"]
    return d


def get_or_create_thread_for_cwd(cwd: str, session_id: str,
                                  title: str = None) -> dict:
    """Find an active thread for this working directory, or create one."""
    thread = find_thread_by_cwd(cwd)
    if thread:
        link_session_to_thread(thread["id"], session_id)
        return thread

    if not title:
        dir_name = Path(cwd).name
        title = f"Work in {dir_name}"

    thread = add_thread(title, session_id=session_id)
    return thread


def find_tasks_by_project_or_cwd(cwd: str) -> list:
    """Find active tasks that match a working directory."""
    project_id = detect_project_from_cwd(cwd)
    if not project_id:
        return []
    tasks = _get_adapter().list(
        filters={"project_id": project_id, "status": "active"},
        limit=10000
    )
    # Also get todo tasks
    todo_tasks = _get_adapter().list(
        filters={"project_id": project_id, "status": "todo"},
        limit=10000
    )
    # Combine and sort by priority
    all_tasks = list(tasks) + list(todo_tasks)
    result = [_to_dict(t) for t in all_tasks]
    result.sort(key=lambda t: (t.get("priority", 3), t.get("created", "")))
    return result


# ── Activity ────────────────────────────────────────────

def get_activity(limit: int = 30) -> list:
    """Get recent work activity events."""
    if not ACTIVITY_FILE.exists():
        return []
    try:
        with open(ACTIVITY_FILE, "r") as f:
            events = yaml.safe_load(f) or []
        return events[:limit]
    except Exception:
        return []


def notify_initiative_event(action: str, title: str, **kwargs) -> None:
    """Send an initiative event to the dashboard SSE stream."""
    event = {
        "action": action,
        "title": title,
        "ts": datetime.now().isoformat(),
    }
    event.update(kwargs)
    _notify_dashboard(event)


# ── Bulk / summary ──────────────────────────────────────

def load_all() -> dict:
    """Load all work data."""
    return {
        "version": "3.0",
        "tasks": get_all_tasks(),
        "projects": get_all_projects(),
        "goals": get_all_goals(),
        "threads": get_all_threads(),
        "inbox": get_inbox(),
    }


def summary() -> dict:
    """Quick summary stats."""
    return _get_adapter().summary()


# ── Move / Migration ────────────────────────────────────

def move_tasks_to_project(task_ids: list[str], target_project: str) -> list[dict]:
    """Move tasks (and their subtasks) to a new project, re-IDing them.

    Returns list of dicts with old_id and new_id for each moved task.
    """
    adapter = _get_adapter()
    conn = adapter._conn

    prefix = adapter._project_prefix(target_project)
    moved = []

    # Build full set of IDs to move (including subtasks)
    ids_to_move = set()
    for tid in task_ids:
        ids_to_move.add(tid)
        sub_rows = conn.execute(
            "SELECT id FROM tasks WHERE parent_id = ?", (tid,)
        ).fetchall()
        for r in sub_rows:
            ids_to_move.add(r["id"])

    # Sort: parents first, then subtasks
    sorted_ids = sorted(ids_to_move, key=lambda x: (x.count("."), x))

    id_map = {}
    now = _now()

    for old_id in sorted_ids:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (old_id,)).fetchone()
        if not row:
            continue

        is_subtask = "." in old_id

        if is_subtask:
            old_parent = old_id.rsplit(".", 1)[0]
            new_parent = id_map.get(old_parent, old_parent)
            new_id = adapter._next_subtask_id(new_parent)
        else:
            new_id = adapter._next_scoped_id(prefix)

        id_map[old_id] = new_id

        # Prepare tags (strip old initiative tags)
        old_tags_str = row["tags"]
        old_tags = json.loads(old_tags_str) if old_tags_str else []
        new_tags = [t for t in old_tags if not t.startswith("initiative:")] if old_tags else None

        # Create new task row with new ID
        conn.execute(
            "INSERT INTO tasks "
            "(id, title, status, priority, project_id, description, "
            " assigned_to, created_by, created_at, started_at, completed_at, "
            " due_at, parent_id, pipeline, pipeline_stage, recurrence, tags, "
            " version, modified_at) "
            "SELECT ?, title, status, priority, ?, description, "
            " assigned_to, created_by, created_at, started_at, completed_at, "
            " due_at, ?, pipeline, pipeline_stage, recurrence, ?, "
            " version, ? "
            "FROM tasks WHERE id = ?",
            (
                new_id, target_project,
                id_map.get(row["parent_id"], row["parent_id"]) if is_subtask else row["parent_id"],
                json.dumps(new_tags) if new_tags else None,
                now, old_id,
            ),
        )

        # Move handoff
        ho_row = conn.execute(
            "SELECT * FROM task_handoffs WHERE task_id = ?", (old_id,)
        ).fetchone()
        if ho_row:
            conn.execute(
                "INSERT OR REPLACE INTO task_handoffs "
                "(task_id, state, next_step, files, decisions, blockers, session_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, ho_row["state"], ho_row["next_step"], ho_row["files"],
                 ho_row["decisions"], ho_row["blockers"], ho_row["session_id"],
                 ho_row["timestamp"]),
            )
            conn.execute("DELETE FROM task_handoffs WHERE task_id = ?", (old_id,))

        # Delete old task
        conn.execute("DELETE FROM tasks WHERE id = ?", (old_id,))

        moved.append({"old_id": old_id, "new_id": new_id})

    conn.commit()
    return moved


def migrate_task_ids() -> dict:
    """Migrate old t1, t2, ... IDs to new project-scoped IDs.

    Delegates to old engine for this one-time migration tool.
    """
    try:
        import engine as _old_engine
        return _old_engine.migrate_task_ids()
    except ImportError:
        # Old engine not available, perform migration directly
        adapter = _get_adapter()
        conn = adapter._conn
        rows = conn.execute("SELECT * FROM tasks").fetchall()
        id_map = {}

        for row in rows:
            old_id = row["id"]
            if "#" in old_id:
                continue

            project = row["project_id"]
            prefix = adapter._project_prefix(project)
            new_id = adapter._next_scoped_id(prefix)

            conn.execute(
                "INSERT INTO tasks "
                "(id, title, status, priority, project_id, description, "
                " assigned_to, created_by, created_at, started_at, completed_at, "
                " due_at, parent_id, pipeline, pipeline_stage, recurrence, tags, "
                " version, modified_at) "
                "SELECT ?, title, status, priority, project_id, description, "
                " assigned_to, created_by, created_at, started_at, completed_at, "
                " due_at, parent_id, pipeline, pipeline_stage, recurrence, tags, "
                " version, ? "
                "FROM tasks WHERE id = ?",
                (new_id, _now(), old_id),
            )
            conn.execute(
                "UPDATE task_handoffs SET task_id = ? WHERE task_id = ?",
                (new_id, old_id),
            )
            conn.execute("DELETE FROM tasks WHERE id = ?", (old_id,))
            id_map[old_id] = new_id

        # Update parent references
        for old, new in id_map.items():
            conn.execute(
                "UPDATE tasks SET parent_id = ? WHERE parent_id = ?",
                (new, old),
            )

        conn.commit()
        return id_map
