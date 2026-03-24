"""
AOS Work Engine — Read/write work files.

Data lives at ~/.aos/work/work.yaml (small mode).
This module handles CRUD for tasks, projects, goals, threads, and inbox.

v2: Project-scoped IDs, fuzzy resolution, subtasks, handoff context.
"""

import fcntl
import os
import re
import tempfile
import yaml
import urllib.request
from datetime import datetime, date
from pathlib import Path
from difflib import SequenceMatcher

DASHBOARD_URL = "http://127.0.0.1:4096"

WORK_DIR = Path.home() / ".aos" / "work"
WORK_FILE = WORK_DIR / "work.yaml"
ACTIVITY_FILE = WORK_DIR / "activity.yaml"
MAX_ACTIVITY = 100  # Keep last N events


LOCK_FILE = WORK_DIR / ".work.lock"


def _load() -> dict:
    """Load work.yaml with shared lock to prevent reading mid-write."""
    if not WORK_FILE.exists():
        return _empty_work()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        try:
            with open(WORK_FILE, "r") as f:
                data = yaml.safe_load(f) or {}
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    # Ensure all top-level keys exist
    for key in ("tasks", "projects", "goals", "threads", "inbox"):
        if key not in data:
            data[key] = []
    return data


def _save(data: dict) -> None:
    """Write work data atomically: lock, write to temp, rename over original."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(WORK_DIR), suffix=".yaml.tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                os.replace(tmp_path, str(WORK_FILE))
            except Exception:
                os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _empty_work() -> dict:
    return {
        "version": "2.0",
        "tasks": [],
        "projects": [],
        "goals": [],
        "threads": [],
        "inbox": [],
    }


# ── ID Generation ─────────────────────────────────────

def _project_prefix(project_id: str | None) -> str:
    """Derive short prefix from project ID.

    aos -> aos
    nuchay -> nch
    chief  -> chief
    None   -> t  (unaffiliated)
    """
    if not project_id:
        return "t"
    # Use the project's short_id if defined, otherwise derive
    data = _load()
    for p in data["projects"]:
        if p["id"] == project_id:
            if p.get("short_id"):
                return p["short_id"]
            break
    # Derive: strip common suffixes, take first word
    clean = re.sub(r'[-_]v\d+$', '', project_id)  # aos-v2 -> aos
    return clean


def _next_scoped_id(tasks: list, prefix: str) -> str:
    """Generate next project-scoped ID: aos#1, aos#2, chief#1, t#1, etc."""
    max_num = 0
    pattern = f"{prefix}#"
    for task in tasks:
        task_id = task.get("id", "")
        if task_id.startswith(pattern):
            # Extract the number part (before any .N subtask suffix)
            rest = task_id[len(pattern):]
            base_num = rest.split(".")[0]
            try:
                num = int(base_num)
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{prefix}#{max_num + 1}"


def _next_subtask_id(tasks: list, parent_id: str) -> str:
    """Generate next subtask ID: aos#3.1, aos#3.2, etc."""
    max_num = 0
    pattern = f"{parent_id}."
    for task in tasks:
        task_id = task.get("id", "")
        if task_id.startswith(pattern):
            try:
                num = int(task_id[len(pattern):])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{parent_id}.{max_num + 1}"


def _next_id(items: list, prefix: str) -> str:
    """Legacy ID generation for non-task entities (p1, g1, th1, i1)."""
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


# ── Fuzzy Resolution ──────────────────────────────────

def resolve_task(query_str: str, tasks: list = None) -> dict | None:
    """Resolve a task by exact ID, partial ID, or fuzzy title match.

    Priority:
    1. Exact ID match (aos#3, t#1, t14)
    2. Partial ID match (aos#3 matches aos#3.1, aos#3.2)
    3. Project-scoped shorthand (3 -> aos#3 if in aos context)
    4. Fuzzy title match ("sse push" -> "SSE push from work engine")

    Returns the best matching task or None.
    """
    if tasks is None:
        tasks = _load()["tasks"]

    if not query_str or not tasks:
        return None

    query_str = query_str.strip()

    # 1. Exact ID match
    for t in tasks:
        if t["id"] == query_str:
            return t

    # 1b. Legacy ID match (t14 -> find task even if renamed)
    for t in tasks:
        if t.get("_legacy_id") == query_str:
            return t

    # 2. Fuzzy title match
    query_lower = query_str.lower()

    # First try substring match
    substring_matches = []
    for t in tasks:
        title_lower = t.get("title", "").lower()
        if query_lower in title_lower:
            substring_matches.append(t)

    if len(substring_matches) == 1:
        return substring_matches[0]

    # If multiple substring matches, pick best by similarity
    if substring_matches:
        best = max(substring_matches,
                   key=lambda t: SequenceMatcher(None, query_lower, t["title"].lower()).ratio())
        return best

    # Full fuzzy match across all tasks
    scored = []
    for t in tasks:
        title_lower = t.get("title", "").lower()
        # Check individual words
        query_words = query_lower.split()
        word_hits = sum(1 for w in query_words if w in title_lower)

        # Sequence similarity
        seq_ratio = SequenceMatcher(None, query_lower, title_lower).ratio()

        # Combined score
        score = (word_hits / max(len(query_words), 1)) * 0.6 + seq_ratio * 0.4
        if score > 0.3:  # Minimum threshold
            scored.append((t, score))

    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    return None


def resolve_task_in_project(query_str: str, project_id: str = None) -> dict | None:
    """Resolve with project context. If query is just a number, scope to project."""
    tasks = _load()["tasks"]

    # If query is just a number and we have project context
    if query_str.isdigit() and project_id:
        prefix = _project_prefix(project_id)
        scoped_id = f"{prefix}#{query_str}"
        for t in tasks:
            if t["id"] == scoped_id:
                return t

    return resolve_task(query_str, tasks)


# ── Context Detection ─────────────────────────────────

# Directory -> project mapping
_PROJECT_DIRS = {
    "aos": "aos",
    "nuchay": "nuchay",
    "chief-ios-app": "chief",
}


def detect_project_from_cwd(cwd: str = None) -> str | None:
    """Detect project from current working directory."""
    if cwd is None:
        cwd = os.getcwd()
    dir_name = Path(cwd).name

    # Direct mapping
    if dir_name in _PROJECT_DIRS:
        return _PROJECT_DIRS[dir_name]

    # Check if cwd is inside a known project directory
    cwd_path = Path(cwd)
    for dir_name, project_id in _PROJECT_DIRS.items():
        project_path = Path.home() / dir_name
        try:
            cwd_path.relative_to(project_path)
            return project_id
        except ValueError:
            continue

    return None


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Activity Log ──────────────────────────────────────

def _log_activity(action: str, task_id: str = None, title: str = None,
                  project: str = None, detail: str = None) -> None:
    """Append an event to the activity log. Fire-and-forget."""
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        event = {
            "ts": _now(),
            "action": action,
        }
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
                        yaml.dump(events, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    os.replace(tmp, str(ACTIVITY_FILE))
                except Exception:
                    os.unlink(tmp)
                    raise
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        # Push to dashboard SSE bus (fire-and-forget)
        _notify_dashboard(event)
    except Exception:
        pass  # Activity log is best-effort


def _notify_dashboard(event: dict) -> None:
    """POST work event to dashboard for instant SSE push. Best-effort."""
    try:
        import json
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


def notify_initiative_event(action: str, title: str, **kwargs) -> None:
    """Send an initiative event to the dashboard SSE stream. Best-effort.

    Actions: initiative_created, initiative_update, phase_completed,
             initiative_completed, gate_check
    """
    event = {
        "action": action,
        "title": title,
        "ts": datetime.now().isoformat(),
    }
    event.update(kwargs)
    _notify_dashboard(event)


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


# ── Task CRUD ─────────────────────────────────────────

def add_task(title: str, priority: int = 3, project: str = None,
             status: str = "todo", tags: list = None, source: str = "manual",
             due: str = None, energy: str = None, context: str = None,
             parent: str = None, source_ref: str = None) -> dict:
    """Add a new task with project-scoped ID."""
    data = _load()

    # If parent specified, this is a subtask
    if parent:
        # Find parent task to inherit project
        parent_task = None
        for t in data["tasks"]:
            if t["id"] == parent:
                parent_task = t
                break

        if parent_task and not project:
            project = parent_task.get("project")

        task_id = _next_subtask_id(data["tasks"], parent)
    else:
        prefix = _project_prefix(project)
        task_id = _next_scoped_id(data["tasks"], prefix)

    task = {
        "id": task_id,
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
    if parent:
        task["parent"] = parent
    if source_ref:
        task["source_ref"] = source_ref
    data["tasks"].append(task)
    _save(data)
    if parent:
        _log_activity("subtask_added", task["id"], title, project, detail=f"under {parent}")
    else:
        _log_activity("task_created", task["id"], title, project)
    return task


def add_subtask(parent_id: str, title: str, priority: int = None,
                status: str = "todo") -> dict | None:
    """Add a subtask to an existing task. Inherits project and priority from parent."""
    data = _load()
    parent = None
    for t in data["tasks"]:
        if t["id"] == parent_id:
            parent = t
            break
    if not parent:
        return None

    if priority is None:
        priority = parent.get("priority", 3)

    return add_task(
        title=title,
        priority=priority,
        project=parent.get("project"),
        status=status,
        parent=parent_id,
        source="subtask",
    )


def complete_task(task_id: str) -> dict | None:
    """Mark a task as done. Auto-cascades parent if all siblings done."""
    data = _load()
    task = None
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["status"] = "done"
            t["completed"] = _now()
            task = t
            break

    if not task:
        return None

    # Cascade: check if parent should auto-complete
    parent_id = task.get("parent")
    if parent_id:
        _cascade_parent(data, parent_id)

    _save(data)
    _log_activity("task_completed", task["id"], task.get("title"), task.get("project"))
    return task


def _cascade_parent(data: dict, parent_id: str) -> None:
    """If all subtasks of parent are done, mark parent as done too."""
    subtasks = [t for t in data["tasks"] if t.get("parent") == parent_id]
    if not subtasks:
        return

    all_done = all(t.get("status") == "done" for t in subtasks)
    if all_done:
        for t in data["tasks"]:
            if t["id"] == parent_id:
                if t.get("status") != "done":
                    t["status"] = "done"
                    t["completed"] = _now()
                    t["auto_completed"] = True
                    # Cascade up further if this parent also has a parent
                    if t.get("parent"):
                        _cascade_parent(data, t["parent"])
                break


def update_task(task_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a task."""
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
            _log_activity("task_started", task["id"], task.get("title"), task.get("project"))
            return task
    return None


def cancel_task(task_id: str) -> dict | None:
    """Cancel a task."""
    result = update_task(task_id, status="cancelled")
    if result:
        _log_activity("task_cancelled", result["id"], result.get("title"), result.get("project"))
    return result


def delete_task(task_id: str) -> bool:
    """Permanently remove a task and its subtasks."""
    data = _load()
    before = len(data["tasks"])
    # Delete the task and any subtasks
    data["tasks"] = [t for t in data["tasks"]
                     if t["id"] != task_id and t.get("parent") != task_id]
    if len(data["tasks"]) < before:
        _save(data)
        return True
    return False


def get_task(task_id: str) -> dict | None:
    """Get a single task by exact ID."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def get_all_tasks() -> list:
    """Get all tasks."""
    return _load()["tasks"]


def get_subtasks(parent_id: str) -> list:
    """Get all subtasks of a parent task."""
    data = _load()
    return [t for t in data["tasks"] if t.get("parent") == parent_id]


def get_task_tree(task_id: str) -> dict | None:
    """Get a task with its subtasks attached."""
    data = _load()
    task = None
    for t in data["tasks"]:
        if t["id"] == task_id:
            task = dict(t)
            break
    if not task:
        return None
    task["subtasks"] = [t for t in data["tasks"] if t.get("parent") == task_id]
    return task


# ── Handoff Context ───────────────────────────────────

def write_handoff(task_id: str, state: str, next_step: str = None,
                  files_touched: list = None, decisions: list = None,
                  blockers: list = None) -> dict | None:
    """Write handoff context for a task. Called by agents before session end."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            handoff = {
                "updated": _now(),
                "state": state,
            }
            if next_step:
                handoff["next_step"] = next_step
            if files_touched:
                handoff["files_touched"] = files_touched
            if decisions:
                handoff["decisions"] = decisions
            if blockers:
                handoff["blockers"] = blockers
            task["handoff"] = handoff
            _save(data)
            _log_activity("handoff_written", task["id"], task.get("title"), task.get("project"),
                          detail=next_step[:80] if next_step else None)
            return task
    return None


def get_handoff(task_id: str) -> dict | None:
    """Get handoff context for a task."""
    task = get_task(task_id)
    if task:
        return task.get("handoff")
    return None


def build_handoff_prompt(task_id: str) -> str | None:
    """Build a dispatch prompt section from a task's handoff context.

    Used by Chief when dispatching agents to continue work on a task.
    Returns a formatted string ready to inject into an agent prompt.
    """
    task = get_task(task_id)
    if not task:
        return None

    handoff = task.get("handoff")
    lines = []
    lines.append(f"Task: {task['id']} -- {task['title']}")

    if task.get("project"):
        lines.append(f"Project: {task['project']}")

    if handoff:
        lines.append("")
        lines.append("CONTEXT FROM PREVIOUS SESSION:")
        lines.append(handoff.get("state", "No state recorded."))

        if handoff.get("next_step"):
            lines.append("")
            lines.append("NEXT STEP:")
            lines.append(handoff["next_step"])

        if handoff.get("files_touched"):
            lines.append("")
            lines.append("FILES TOUCHED:")
            for f in handoff["files_touched"]:
                lines.append(f"  - {f}")

        if handoff.get("decisions"):
            lines.append("")
            lines.append("DECISIONS ALREADY MADE (don't revisit):")
            for d in handoff["decisions"]:
                lines.append(f"  - {d}")

        if handoff.get("blockers"):
            lines.append("")
            lines.append("BLOCKERS:")
            for b in handoff["blockers"]:
                lines.append(f"  - {b}")
    else:
        lines.append("")
        lines.append("No previous handoff context. Starting fresh.")

    lines.append("")
    lines.append("When stopping, update the handoff with: work handoff <task_id> --state '...' --next '...'")

    return "\n".join(lines)


# ── Project CRUD ──────────────────────────────────────

def add_project(title: str, goal: str = None, done_when: str = None,
                appetite: str = None, short_id: str = None) -> dict:
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
    if short_id:
        project["short_id"] = short_id
    data["projects"].append(project)
    _save(data)
    return project


def get_all_projects() -> list:
    return _load()["projects"]


def update_project(project_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a project."""
    data = _load()
    for project in data["projects"]:
        if project["id"] == project_id:
            for key, val in fields.items():
                if val is None and key in project:
                    del project[key]
                elif val is not None:
                    project[key] = val
            _save(data)
            return project
    return None


def delete_project(project_id: str) -> bool:
    """Remove a project. Does NOT delete its tasks."""
    data = _load()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
    if len(data["projects"]) < before:
        _save(data)
        return True
    return False


# ── Goal CRUD ─────────────────────────────────────────

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


def update_goal(goal_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a goal."""
    data = _load()
    for goal in data["goals"]:
        if goal["id"] == goal_id:
            for key, val in fields.items():
                if val is None and key in goal:
                    del goal[key]
                elif val is not None:
                    goal[key] = val
            _save(data)
            return goal
    return None


def delete_goal(goal_id: str) -> bool:
    data = _load()
    before = len(data["goals"])
    data["goals"] = [g for g in data["goals"] if g["id"] != goal_id]
    if len(data["goals"]) < before:
        _save(data)
        return True
    return False


# ── Thread CRUD ───────────────────────────────────────

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


# ── Inbox ─────────────────────────────────────────────

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


def delete_inbox(inbox_id: str) -> bool:
    data = _load()
    before = len(data["inbox"])
    data["inbox"] = [i for i in data["inbox"] if i["id"] != inbox_id]
    if len(data["inbox"]) < before:
        _save(data)
        return True
    return False


# ── Session Linking ───────────────────────────────────

def link_session_to_task(task_id: str, session_id: str, outcome: str = None,
                         date_str: str = None) -> dict | None:
    """Link a session to a task."""
    data = _load()
    for task in data["tasks"]:
        if task["id"] == task_id:
            if "sessions" not in task:
                task["sessions"] = []
            # Dedup
            existing_ids = {s["id"] for s in task["sessions"]}
            if session_id in existing_ids:
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
    """Link a session to a thread."""
    data = _load()
    for thread in data["threads"]:
        if thread["id"] == thread_id:
            if "sessions" not in thread:
                thread["sessions"] = []
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
    """Find an active thread for this working directory, or create one."""
    thread = find_thread_by_cwd(cwd)
    if thread:
        link_session_to_thread(thread["id"], session_id)
        return thread
    data = _load()
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
    """Find active tasks that match a working directory."""
    data = _load()
    project_id = detect_project_from_cwd(cwd)
    if not project_id:
        return []
    active_tasks = [t for t in data["tasks"]
                    if t.get("project") == project_id
                    and t.get("status") in ("active", "todo")]
    return active_tasks


# ── Migration ─────────────────────────────────────────

def migrate_task_ids() -> dict:
    """Migrate old t1, t2, ... IDs to new project-scoped IDs.

    Returns mapping of old_id -> new_id.
    """
    data = _load()
    id_map = {}

    # First pass: rename all tasks
    for task in data["tasks"]:
        old_id = task["id"]
        # Skip if already in new format
        if "#" in old_id:
            continue

        project = task.get("project")
        prefix = _project_prefix(project)
        new_id = _next_scoped_id(data["tasks"], prefix)

        # Store legacy ID for backward compat
        task["_legacy_id"] = old_id
        task["id"] = new_id
        id_map[old_id] = new_id

    # Second pass: update parent references
    for task in data["tasks"]:
        if task.get("parent") and task["parent"] in id_map:
            task["parent"] = id_map[task["parent"]]

    # Third pass: update session links in threads
    # (sessions reference task IDs in some places)

    _save(data)
    return id_map


# ── Bulk accessors ────────────────────────────────────

def load_all() -> dict:
    """Load entire work file. Use for dashboards/reviews."""
    return _load()


def summary() -> dict:
    """Quick summary stats."""
    data = _load()
    tasks = data["tasks"]
    # Exclude subtasks from top-level counts
    top_level = [t for t in tasks if not t.get("parent")]
    return {
        "total_tasks": len(top_level),
        "by_status": _count_by(top_level, "status"),
        "by_priority": _count_by(top_level, "priority"),
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
