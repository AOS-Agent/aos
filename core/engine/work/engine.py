"""
AOS Work Engine — Read/write work data.

Data lives in ~/.aos/data/qareen.db (SQLite, canonical store).
Legacy backup at ~/.aos/work/work.yaml (read-only, not written to).
This module handles CRUD for tasks, projects, goals, threads, and inbox.

v3: SQLite backend via qareen.db. Same API as v2.
v2: Project-scoped IDs, fuzzy resolution, subtasks, handoff context.
"""

import fcntl
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import yaml
import urllib.request
from datetime import datetime, date
from pathlib import Path
from difflib import SequenceMatcher

_gh_log = logging.getLogger("work.github")

DASHBOARD_URL = "http://127.0.0.1:4096"
AOS_REPO = "hishamalhadi/aos"  # GitHub repo for issue sync


# ── GitHub Issues sync (public roadmap) ────────────────────
# Only syncs: title, priority label, open/closed status.
# No handoff, notes, sessions, or personal data ever leaves the machine.

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
        # Find the issue number by searching
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

WORK_DIR = Path.home() / ".aos" / "work"
WORK_FILE = WORK_DIR / "work.yaml"
ACTIVITY_FILE = WORK_DIR / "activity.yaml"
LIVE_CONTEXT_FILE = WORK_DIR / ".live-context.json"
MAX_ACTIVITY = 100  # Keep last N events

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

LOCK_FILE = WORK_DIR / ".work.lock"


# ── SQLite Connection ───────────────────────────────────

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    """Get or create a SQLite connection with WAL mode and foreign keys."""
    global _conn
    if _conn is not None:
        return _conn
    _conn = sqlite3.connect(str(DB_PATH))
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.row_factory = sqlite3.Row
    return _conn


def _json_loads(val: str | None, default=None):
    """Safely parse a JSON string."""
    if not val:
        return default if default is not None else []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _json_dumps(val: list | dict | None) -> str | None:
    """Serialize to JSON string or return None."""
    if val is None:
        return None
    if isinstance(val, list) and len(val) == 0:
        return None
    return json.dumps(val)


def _row_to_task(row: sqlite3.Row) -> dict:
    """Convert a tasks table row to a dict matching the YAML-era format.

    This is the canonical mapping. Every consumer (cli.py, inject_context.py,
    session_close.py, query.py) expects these exact keys.
    """
    task_id = row["id"]
    tags = _json_loads(row["tags"])

    task = {
        "id": task_id,
        "title": row["title"],
        "status": row["status"] or "todo",
        "priority": row["priority"] if row["priority"] is not None else 3,
        "created": row["created_at"] or "",
        "source": row["created_by"] or "manual",
    }

    if row["project_id"]:
        task["project"] = row["project_id"]
    if row["started_at"]:
        task["started"] = row["started_at"]
    if row["completed_at"]:
        task["completed"] = row["completed_at"]
    if row["due_at"]:
        task["due"] = row["due_at"]
    if row["parent_id"]:
        task["parent"] = row["parent_id"]
    if tags:
        task["tags"] = tags
    if row["description"]:
        task["notes"] = row["description"]
    if row["assigned_to"]:
        task["assigned_to"] = row["assigned_to"]
    if row["pipeline"]:
        task["pipeline"] = row["pipeline"]
    if row["recurrence"]:
        task["recurrence"] = row["recurrence"]

    # Load handoff from task_handoffs table
    conn = _db()
    ho_row = conn.execute(
        "SELECT * FROM task_handoffs WHERE task_id = ?", (task_id,)
    ).fetchone()
    if ho_row:
        handoff = {
            "updated": ho_row["timestamp"] or "",
            "state": ho_row["state"] or "",
        }
        if ho_row["next_step"]:
            handoff["next_step"] = ho_row["next_step"]
        files = _json_loads(ho_row["files"])
        if files:
            handoff["files_touched"] = files
        decisions = _json_loads(ho_row["decisions"])
        if decisions:
            handoff["decisions"] = decisions
        blockers = _json_loads(ho_row["blockers"])
        if blockers:
            handoff["blockers"] = blockers
        task["handoff"] = handoff

    # Load session links from session_tasks table
    sess_rows = conn.execute(
        "SELECT st.session_id, s.started_at, s.outcome "
        "FROM session_tasks st "
        "LEFT JOIN sessions s ON st.session_id = s.id "
        "WHERE st.task_id = ?",
        (task_id,)
    ).fetchall()
    if sess_rows:
        sessions = []
        for sr in sess_rows:
            entry = {"id": sr["session_id"]}
            if sr["started_at"]:
                entry["date"] = sr["started_at"][:10]
            if sr["outcome"]:
                entry["outcome"] = sr["outcome"]
            sessions.append(entry)
        task["sessions"] = sessions

    return task


def _row_to_project(row: sqlite3.Row) -> dict:
    """Convert a projects table row to a dict matching the YAML-era format."""
    project = {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"] or "active",
    }
    if row["description"]:
        project["description"] = row["description"]
    if row["path"]:
        project["path"] = row["path"]
    if row["goal"]:
        project["goal"] = row["goal"]
    if row["done_when"]:
        project["done_when"] = row["done_when"]
    stages = _json_loads(row["stages"])
    if stages:
        project["stages"] = stages
    if row["current_stage"]:
        project["current_stage"] = row["current_stage"]
    if row["telegram_bot_key"]:
        project["telegram_bot_key"] = row["telegram_bot_key"]
    if row["telegram_chat_key"]:
        project["telegram_chat_key"] = row["telegram_chat_key"]
    if row["telegram_forum_topic"]:
        project["telegram_forum_topic"] = row["telegram_forum_topic"]
    return project


def _row_to_goal(row: sqlite3.Row) -> dict:
    """Convert a goals table row to a dict matching the YAML-era format."""
    goal = {
        "id": row["id"],
        "title": row["title"],
        "status": "active",  # goals don't have status column, default active
    }
    if row["weight"]:
        goal["weight"] = row["weight"]
    if row["description"]:
        goal["description"] = row["description"]
    if row["project_id"]:
        goal["project"] = row["project_id"]

    # Load key results
    conn = _db()
    kr_rows = conn.execute(
        "SELECT * FROM key_results WHERE goal_id = ?", (row["id"],)
    ).fetchall()
    if kr_rows:
        goal["key_results"] = [
            {
                "title": kr["title"],
                "progress": kr["progress"] or 0,
                "target": kr["target"],
            }
            for kr in kr_rows
        ]
    return goal


def _row_to_thread(row: sqlite3.Row) -> dict:
    """Convert a threads table row to a dict matching the YAML-era format."""
    thread = {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"] or "exploring",
    }
    if row["created_at"]:
        thread["started"] = row["created_at"]
    if row["project_id"]:
        thread["project"] = row["project_id"]
    return thread


def _row_to_inbox(row: sqlite3.Row) -> dict:
    """Convert an inbox table row to a dict matching the YAML-era format."""
    return {
        "id": row["id"],
        "text": row["text"],
        "captured": row["captured_at"] or "",
        "source": "manual",
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
    # Check if the project has a custom short_id — not stored in DB currently
    # Derive: strip common suffixes, take first word
    clean = re.sub(r'[-_]v\d+$', '', project_id)  # aos-v2 -> aos
    return clean


def _next_scoped_id(prefix: str) -> str:
    """Generate next project-scoped ID: aos#1, aos#2, chief#1, t#1, etc.

    Reads from the database to find the maximum existing ID number.
    """
    conn = _db()
    pattern = f"{prefix}#%"
    rows = conn.execute(
        "SELECT id FROM tasks WHERE id LIKE ?", (pattern,)
    ).fetchall()

    max_num = 0
    pat = f"{prefix}#"
    for row in rows:
        task_id = row["id"]
        if task_id.startswith(pat):
            rest = task_id[len(pat):]
            base_num = rest.split(".")[0]
            try:
                num = int(base_num)
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{prefix}#{max_num + 1}"


def _next_subtask_id(parent_id: str) -> str:
    """Generate next subtask ID: aos#3.1, aos#3.2, etc."""
    conn = _db()
    pattern = f"{parent_id}.%"
    rows = conn.execute(
        "SELECT id FROM tasks WHERE id LIKE ?", (pattern,)
    ).fetchall()

    max_num = 0
    pat = f"{parent_id}."
    for row in rows:
        task_id = row["id"]
        if task_id.startswith(pat):
            try:
                num = int(task_id[len(pat):])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{parent_id}.{max_num + 1}"


def _next_id(prefix: str) -> str:
    """Legacy ID generation for non-task entities (p1, g1, th1, i1).

    Reads from the appropriate table based on prefix.
    """
    conn = _db()
    table_map = {
        "p": "projects",
        "g": "goals",
        "th": "threads",
        "i": "inbox",
    }
    table = table_map.get(prefix, "tasks")
    rows = conn.execute(f"SELECT id FROM {table}").fetchall()

    max_num = 0
    for row in rows:
        item_id = row["id"]
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
        tasks = get_all_tasks()

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
    tasks = get_all_tasks()

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
                        f.flush()
                        os.fsync(f.fileno())
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


def _sync_initiative_checkbox(task: dict) -> None:
    """If task has source_ref to an initiative doc, check off matching checkbox.

    Best-effort: never crashes, never blocks. This is the SINGLE sync point —
    any code path that completes a task gets initiative sync for free.
    """
    source_ref = task.get("source_ref")
    if not source_ref:
        return
    try:
        import re as _re
        doc_path = Path.home() / source_ref.lstrip("~/")
        if not doc_path.exists():
            # Try as relative to home
            doc_path = Path.home() / source_ref
        if not doc_path.exists():
            return

        content = doc_path.read_text()
        title = task.get("title", "")
        task_id = task.get("id", "")

        # Match checkbox by task ID reference (e.g., "→ aos#15") or title substring
        updated = False
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if not _re.match(r'\s*- \[ \]', line):
                continue
            # Match by task ID (most reliable)
            if task_id and task_id in line:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                updated = True
                break
            # Match by title (fuzzy — first 40 chars)
            if title and title[:40].lower() in line.lower():
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                updated = True
                break

        if updated:
            # Also update the 'updated:' date in frontmatter
            today = datetime.now().strftime("%Y-%m-%d")
            new_content = "\n".join(lines)
            new_content = _re.sub(
                r'^updated:.*$', f'updated: {today}',
                new_content, count=1, flags=_re.MULTILINE
            )
            # Atomic write
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
        pass  # Best-effort — never crash the work engine


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
             parent: str = None, source_ref: str = None,
             notes: str = None) -> dict:
    """Add a new task with project-scoped ID."""
    conn = _db()

    # If parent specified, this is a subtask
    if parent:
        # Find parent task to inherit project
        parent_row = conn.execute(
            "SELECT project_id FROM tasks WHERE id = ?", (parent,)
        ).fetchone()
        if parent_row and not project:
            project = parent_row["project_id"]

        task_id = _next_subtask_id(parent)
    else:
        prefix = _project_prefix(project)
        task_id = _next_scoped_id(prefix)

    now = _now()

    # Build description from notes + extra context
    description = notes
    # Store energy and context in description as metadata if present
    # (DB doesn't have dedicated columns for these)
    meta_parts = []
    if energy:
        meta_parts.append(f"energy:{energy}")
    if context:
        meta_parts.append(f"context:{context}")
    if source_ref:
        meta_parts.append(f"source_ref:{source_ref}")
    if meta_parts and description:
        description = description + "\n\n<!-- meta: " + ", ".join(meta_parts) + " -->"
    elif meta_parts:
        description = "<!-- meta: " + ", ".join(meta_parts) + " -->"

    conn.execute(
        "INSERT INTO tasks "
        "(id, title, status, priority, project_id, description, "
        " created_by, created_at, due_at, parent_id, tags, "
        " version, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            task_id, title, status, priority, project,
            description, source, now, due, parent,
            _json_dumps(tags), now,
        ),
    )

    # FTS sync
    _sync_fts(task_id, title, description)

    conn.commit()

    # Build the return dict to match YAML-era format
    task = {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": priority,
        "created": now,
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
    if notes:
        task["notes"] = notes

    if parent:
        _log_activity("subtask_added", task["id"], title, project, detail=f"under {parent}")
    else:
        _log_activity("task_created", task["id"], title, project)

        # Sync to GitHub Issues (aos project only, parent tasks only)
        if project == "aos":
            _gh_create_issue(task["id"], title, priority)

    return task


def _sync_fts(task_id: str, title: str, description: str | None) -> None:
    """Keep FTS index in sync."""
    conn = _db()
    row = conn.execute(
        "SELECT rowid FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row:
        rowid = row[0]
        try:
            conn.execute(
                "INSERT INTO tasks_fts(tasks_fts, rowid, title, description) "
                "VALUES('delete', ?, ?, ?)",
                (rowid, title, description or ""),
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "INSERT INTO tasks_fts(rowid, title, description) "
                "VALUES(?, ?, ?)",
                (rowid, title, description or ""),
            )
        except sqlite3.OperationalError:
            pass


def add_subtask(parent_id: str, title: str, priority: int = None,
                status: str = "todo") -> dict | None:
    """Add a subtask to an existing task. Inherits project and priority from parent."""
    conn = _db()
    parent = conn.execute(
        "SELECT id, project_id, priority FROM tasks WHERE id = ?", (parent_id,)
    ).fetchone()
    if not parent:
        return None

    if priority is None:
        priority = parent["priority"] if parent["priority"] is not None else 3

    return add_task(
        title=title,
        priority=priority,
        project=parent["project_id"],
        status=status,
        parent=parent_id,
        source="subtask",
    )


def complete_task(task_id: str) -> dict | None:
    """Mark a task as done. Auto-cascades parent if all siblings done.
    If task has source_ref pointing to an initiative, updates the checkbox."""
    conn = _db()
    now = _now()

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None

    conn.execute(
        "UPDATE tasks SET status = 'done', completed_at = ?, modified_at = ? WHERE id = ?",
        (now, now, task_id),
    )
    conn.commit()

    task = _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    # Cascade: check if parent should auto-complete
    parent_id = row["parent_id"]
    if parent_id:
        _cascade_parent(parent_id)

    _log_activity("task_completed", task["id"], task.get("title"), task.get("project"))

    # Sync to GitHub Issues (close the issue)
    if task.get("project") == "aos":
        _gh_close_issue(task["id"])

    # Clear live context if this was the active task
    clear_live_context(task["id"])

    # Sync initiative checkbox (best-effort, never crashes)
    _sync_initiative_checkbox(task)

    # Check if a phase just completed (parent auto-cascaded)
    if parent_id:
        parent_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (parent_id,)).fetchone()
        if parent_row and parent_row["status"] == "done":
            parent_task = _row_to_task(parent_row)
            notify_initiative_event(
                "phase_completed",
                parent_task.get("title", parent_id),
                task_id=parent_id,
                project=parent_task.get("project"),
            )
            _sync_initiative_checkbox(parent_task)
            task["auto_completed"] = True  # Signal for CLI display

    return task


def _cascade_parent(parent_id: str) -> None:
    """If all subtasks of parent are done, mark parent as done too."""
    conn = _db()

    subtasks = conn.execute(
        "SELECT id, status FROM tasks WHERE parent_id = ?", (parent_id,)
    ).fetchall()
    if not subtasks:
        return

    all_done = all(r["status"] == "done" for r in subtasks)
    if all_done:
        parent_row = conn.execute(
            "SELECT id, status, parent_id FROM tasks WHERE id = ?", (parent_id,)
        ).fetchone()
        if parent_row and parent_row["status"] != "done":
            now = _now()
            conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ?, modified_at = ? WHERE id = ?",
                (now, now, parent_id),
            )
            conn.commit()
            # Cascade up further if this parent also has a parent
            if parent_row["parent_id"]:
                _cascade_parent(parent_row["parent_id"])


def update_task(task_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a task."""
    conn = _db()

    row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None

    # Map YAML-style field names to DB column names
    field_map = {
        "title": "title",
        "status": "status",
        "priority": "priority",
        "project": "project_id",
        "started": "started_at",
        "completed": "completed_at",
        "due": "due_at",
        "parent": "parent_id",
        "notes": "description",
        "assigned_to": "assigned_to",
        "tags": "tags",
        "pipeline": "pipeline",
        "recurrence": "recurrence",
        "source": "created_by",
        # Direct DB column names also work
        "project_id": "project_id",
        "started_at": "started_at",
        "completed_at": "completed_at",
        "due_at": "due_at",
        "parent_id": "parent_id",
        "description": "description",
        "created_by": "created_by",
    }

    sets = []
    params = []
    for key, val in fields.items():
        col = field_map.get(key)
        if col:
            if col == "tags":
                sets.append(f"{col} = ?")
                params.append(_json_dumps(val) if val else None)
            else:
                sets.append(f"{col} = ?")
                params.append(val)

    if not sets:
        return _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    sets.append("modified_at = ?")
    params.append(_now())
    params.append(task_id)

    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()

    updated_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(updated_row) if updated_row else None


def start_task(task_id: str, session_id: str = None) -> dict | None:
    """Move a task to active status and set it as the live work context."""
    conn = _db()
    now = _now()

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None

    conn.execute(
        "UPDATE tasks SET status = 'active', started_at = ?, modified_at = ? WHERE id = ?",
        (now, now, task_id),
    )
    conn.commit()

    task = _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    _log_activity("task_started", task["id"], task.get("title"), task.get("project"))
    set_live_context(task, session_id=session_id)
    return task


# ── Live Context ─────────────────────────────────────
# The "workbench" — declares what's being worked on RIGHT NOW.
# Written by `work start`, cleared by `work done`/`work stop`.
# The Stop hook reads this to attribute work to tasks — no inference needed.


def set_live_context(task: dict, session_id: str = None) -> None:
    """Set the live work context. Called when a task is started."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    ctx = {
        "task_id": task["id"],
        "title": task.get("title", ""),
        "project": task.get("project"),
        "started_at": _now(),
        "session_id": session_id,
        "cwd": os.getcwd(),
    }
    LIVE_CONTEXT_FILE.write_text(json.dumps(ctx, indent=2))


def clear_live_context(task_id: str = None) -> dict | None:
    """Clear the live context. Returns the old context if it existed.
    If task_id provided, only clears if it matches (prevents clearing wrong task)."""
    if not LIVE_CONTEXT_FILE.exists():
        return None
    try:
        ctx = json.loads(LIVE_CONTEXT_FILE.read_text())
        if task_id and ctx.get("task_id") != task_id:
            return None  # Different task is active — don't touch
        LIVE_CONTEXT_FILE.unlink()
        return ctx
    except Exception:
        return None


def get_live_context() -> dict | None:
    """Read current live context. Returns None if nothing active."""
    if not LIVE_CONTEXT_FILE.exists():
        return None
    try:
        return json.loads(LIVE_CONTEXT_FILE.read_text())
    except Exception:
        return None


def cancel_task(task_id: str) -> dict | None:
    """Cancel a task."""
    result = update_task(task_id, status="cancelled")
    if result:
        _log_activity("task_cancelled", result["id"], result.get("title"), result.get("project"))
        clear_live_context(task_id)  # Clear if this was the active task
    return result


def delete_task(task_id: str) -> bool:
    """Permanently remove a task and its subtasks."""
    conn = _db()

    # Check if task exists
    row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return False

    # Delete subtasks first
    conn.execute("DELETE FROM task_handoffs WHERE task_id IN (SELECT id FROM tasks WHERE parent_id = ?)", (task_id,))
    conn.execute("DELETE FROM session_tasks WHERE task_id IN (SELECT id FROM tasks WHERE parent_id = ?)", (task_id,))
    conn.execute("DELETE FROM tasks WHERE parent_id = ?", (task_id,))

    # Delete the task itself
    conn.execute("DELETE FROM task_handoffs WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM session_tasks WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return True


def get_task(task_id: str) -> dict | None:
    """Get a single task by exact ID."""
    conn = _db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    return _row_to_task(row)


def get_all_tasks() -> list:
    """Get all tasks."""
    conn = _db()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    return [_row_to_task(r) for r in rows]


def get_subtasks(parent_id: str) -> list:
    """Get all subtasks of a parent task."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE parent_id = ? ORDER BY id", (parent_id,)
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_task_tree(task_id: str) -> dict | None:
    """Get a task with its subtasks attached."""
    conn = _db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    task = _row_to_task(row)
    sub_rows = conn.execute(
        "SELECT * FROM tasks WHERE parent_id = ? ORDER BY id", (task_id,)
    ).fetchall()
    task["subtasks"] = [_row_to_task(r) for r in sub_rows]
    return task


# ── Handoff Context ───────────────────────────────────

def write_handoff(task_id: str, state: str, next_step: str = None,
                  files_touched: list = None, decisions: list = None,
                  blockers: list = None) -> dict | None:
    """Write handoff context for a task. Called by agents before session end."""
    conn = _db()
    now = _now()

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None

    conn.execute(
        "INSERT OR REPLACE INTO task_handoffs "
        "(task_id, state, next_step, files, decisions, blockers, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            task_id, state, next_step or "",
            _json_dumps(files_touched),
            _json_dumps(decisions),
            _json_dumps(blockers),
            now,
        ),
    )
    conn.commit()

    task = _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    _log_activity("handoff_written", task["id"], task.get("title"), task.get("project"),
                  detail=next_step[:80] if next_step else None)
    return task


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
                appetite: str = None, short_id: str = None,
                initiative: str = None, project_id: str = None) -> dict:
    """Create a new project.

    Args:
        title: Project title
        goal: Goal ID to link to
        done_when: Definition of done
        appetite: Time budget (e.g., '6-hours', '2-weeks')
        short_id: Custom prefix for task IDs (e.g., 'uc' for unified-comms)
        initiative: Initiative slug — links to vault/knowledge/initiatives/{slug}.md
        project_id: Custom project ID (defaults to auto-generated p1, p2, etc.)
    """
    conn = _db()

    # Use custom project_id or auto-generate
    pid = project_id if project_id else _next_id("p")

    # Prevent duplicate project IDs
    existing = conn.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone()
    if existing:
        raise ValueError(f"Project '{pid}' already exists")

    now = _now()

    # Build description from extra fields not in schema
    desc_parts = []
    if appetite:
        desc_parts.append(f"appetite:{appetite}")
    if short_id:
        desc_parts.append(f"short_id:{short_id}")
    if initiative:
        desc_parts.append(f"initiative:{initiative}")
    description = ", ".join(desc_parts) if desc_parts else None

    conn.execute(
        "INSERT INTO projects "
        "(id, title, description, status, goal, done_when, version, modified_at) "
        "VALUES (?, ?, ?, 'active', ?, ?, 1, ?)",
        (pid, title, description, goal, done_when, now),
    )
    conn.commit()

    project = {
        "id": pid,
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
    if initiative:
        project["initiative"] = initiative
    return project


def get_all_projects() -> list:
    """Get all projects."""
    conn = _db()
    rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    return [_row_to_project(r) for r in rows]


def update_project(project_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a project."""
    conn = _db()

    row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        return None

    field_map = {
        "title": "title",
        "description": "description",
        "status": "status",
        "path": "path",
        "goal": "goal",
        "done_when": "done_when",
        "current_stage": "current_stage",
        "stages": "stages",
    }

    sets = []
    params = []
    for key, val in fields.items():
        col = field_map.get(key)
        if col:
            if col == "stages":
                sets.append(f"{col} = ?")
                params.append(_json_dumps(val) if val else None)
            else:
                sets.append(f"{col} = ?")
                params.append(val)

    if not sets:
        updated_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_project(updated_row) if updated_row else None

    sets.append("modified_at = ?")
    params.append(_now())
    params.append(project_id)

    conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()

    updated_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_project(updated_row) if updated_row else None


def delete_project(project_id: str) -> bool:
    """Remove a project. Does NOT delete its tasks."""
    conn = _db()
    cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    return cur.rowcount > 0


def move_tasks_to_project(task_ids: list[str], target_project: str) -> list[dict]:
    """Move tasks (and their subtasks) to a new project, re-IDing them.

    Returns list of dicts with old_id and new_id for each moved task.
    """
    conn = _db()
    prefix = _project_prefix(target_project)
    moved = []

    # Build map of tasks to move (including subtasks)
    ids_to_move = set()
    for tid in task_ids:
        ids_to_move.add(tid)
        # Find subtasks
        sub_rows = conn.execute(
            "SELECT id FROM tasks WHERE parent_id = ?", (tid,)
        ).fetchall()
        for r in sub_rows:
            ids_to_move.add(r["id"])

    # Sort: parents first, then subtasks
    sorted_ids = sorted(ids_to_move, key=lambda x: (x.count("."), x))

    # Map old parent IDs to new parent IDs for subtask re-parenting
    id_map = {}
    now = _now()

    for old_id in sorted_ids:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (old_id,)).fetchone()
        if not row:
            continue

        is_subtask = "." in old_id

        if is_subtask:
            # Find the parent's new ID
            old_parent = old_id.rsplit(".", 1)[0]
            new_parent = id_map.get(old_parent, old_parent)
            new_id = _next_subtask_id(new_parent)
        else:
            new_id = _next_scoped_id(prefix)

        id_map[old_id] = new_id

        # Update the task: new ID, new project, remove old tags
        old_tags = _json_loads(row["tags"])
        new_tags = [t for t in old_tags if not t.startswith("initiative:")] if old_tags else None

        # Create new task with new ID
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
                _json_dumps(new_tags),
                now, old_id,
            ),
        )

        # Move handoff
        ho_row = conn.execute("SELECT * FROM task_handoffs WHERE task_id = ?", (old_id,)).fetchone()
        if ho_row:
            conn.execute(
                "INSERT OR REPLACE INTO task_handoffs "
                "(task_id, state, next_step, files, decisions, blockers, session_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, ho_row["state"], ho_row["next_step"], ho_row["files"],
                 ho_row["decisions"], ho_row["blockers"], ho_row["session_id"], ho_row["timestamp"]),
            )
            conn.execute("DELETE FROM task_handoffs WHERE task_id = ?", (old_id,))

        # Delete old task
        conn.execute("DELETE FROM tasks WHERE id = ?", (old_id,))

        moved.append({"old_id": old_id, "new_id": new_id})

    conn.commit()
    return moved


# ── Goal CRUD ─────────────────────────────────────────

def add_goal(title: str, goal_type: str = "committed", weight: float = None) -> dict:
    conn = _db()
    gid = _next_id("g")

    conn.execute(
        "INSERT INTO goals (id, title, weight, description) VALUES (?, ?, ?, ?)",
        (gid, title, weight, goal_type),
    )
    conn.commit()

    goal = {
        "id": gid,
        "title": title,
        "status": "active",
        "type": goal_type,
    }
    if weight is not None:
        goal["weight"] = weight
    return goal


def get_all_goals() -> list:
    conn = _db()
    rows = conn.execute("SELECT * FROM goals ORDER BY id").fetchall()
    return [_row_to_goal(r) for r in rows]


def update_goal(goal_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a goal."""
    conn = _db()

    row = conn.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        return None

    field_map = {
        "title": "title",
        "description": "description",
        "weight": "weight",
    }

    sets = []
    params = []
    for key, val in fields.items():
        col = field_map.get(key)
        if col:
            sets.append(f"{col} = ?")
            params.append(val)

    if not sets:
        return _row_to_goal(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    params.append(goal_id)
    conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()

    return _row_to_goal(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())


def delete_goal(goal_id: str) -> bool:
    conn = _db()
    conn.execute("DELETE FROM key_results WHERE goal_id = ?", (goal_id,))
    cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Thread CRUD ───────────────────────────────────────

def add_thread(title: str, session_id: str = None) -> dict:
    conn = _db()
    tid = _next_id("th")
    now = _today()

    conn.execute(
        "INSERT INTO threads (id, title, status, created_at) VALUES (?, ?, 'exploring', ?)",
        (tid, title, now),
    )
    conn.commit()

    thread = {
        "id": tid,
        "title": title,
        "status": "exploring",
        "started": now,
    }
    if session_id:
        thread["sessions"] = [session_id]
    return thread


def get_thread(thread_id: str) -> dict | None:
    """Get a single thread by ID."""
    conn = _db()
    row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if not row:
        return None
    return _row_to_thread(row)


def update_thread(thread_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a thread."""
    conn = _db()

    row = conn.execute("SELECT id FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if not row:
        return None

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
        return _row_to_thread(conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone())

    params.append(thread_id)
    conn.execute(f"UPDATE threads SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()

    return _row_to_thread(conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone())


def promote_thread(thread_id: str, project_title: str = None,
                   goal: str = None) -> dict | None:
    """Promote a thread to a project. Returns the created project."""
    conn = _db()
    row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if not row:
        return None

    title = project_title or row["title"]
    conn.execute("UPDATE threads SET status = 'promoted' WHERE id = ?", (thread_id,))
    conn.commit()

    project = add_project(title, goal=goal)
    # Link the thread to the project
    update_thread(thread_id, promoted_to=None)  # promoted_to not in DB schema
    return project


def get_all_threads() -> list:
    conn = _db()
    rows = conn.execute("SELECT * FROM threads ORDER BY created_at DESC").fetchall()
    return [_row_to_thread(r) for r in rows]


def find_thread_by_cwd(cwd: str) -> dict | None:
    """Find an active thread associated with a working directory.

    Note: The threads table doesn't have a 'cwd' column, so this returns None.
    Thread-CWD association was a YAML-only feature.
    """
    # In the DB schema, threads don't have a cwd column.
    # Return None — session_close will create threads as needed.
    return None


# ── Inbox ─────────────────────────────────────────────

def add_inbox(text: str, source: str = "manual", confidence: float = None) -> dict:
    conn = _db()
    iid = _next_id("i")
    now = _now()

    conn.execute(
        "INSERT INTO inbox (id, text, captured_at) VALUES (?, ?, ?)",
        (iid, text, now),
    )
    conn.commit()

    item = {
        "id": iid,
        "text": text,
        "captured": now,
        "source": source,
    }
    if confidence is not None:
        item["confidence"] = confidence
    return item


def get_inbox() -> list:
    conn = _db()
    rows = conn.execute("SELECT * FROM inbox ORDER BY captured_at DESC").fetchall()
    return [_row_to_inbox(r) for r in rows]


def promote_inbox(inbox_id: str, as_title: str = None) -> dict | None:
    """Promote an inbox item to a task. Returns the created task."""
    conn = _db()
    row = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if not row:
        return None

    title = as_title or row["text"]
    conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    conn.commit()
    return add_task(title, source="inbox")


def delete_inbox(inbox_id: str) -> bool:
    conn = _db()
    cur = conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Session Linking ───────────────────────────────────

def link_session_to_task(task_id: str, session_id: str, outcome: str = None,
                         date_str: str = None) -> dict | None:
    """Link a session to a task."""
    conn = _db()

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None

    # Use session_tasks table for linking
    existing = conn.execute(
        "SELECT 1 FROM session_tasks WHERE session_id = ? AND task_id = ?",
        (session_id, task_id),
    ).fetchone()

    if not existing:
        conn.execute(
            "INSERT INTO session_tasks (session_id, task_id, relation) VALUES (?, ?, 'worked_on')",
            (session_id, task_id),
        )

    # Also ensure a sessions row exists (lightweight)
    sess_exists = conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not sess_exists:
        conn.execute(
            "INSERT INTO sessions (id, status, started_at, task_id, outcome) VALUES (?, 'active', ?, ?, ?)",
            (session_id, date_str or _now(), task_id, outcome),
        )
    elif outcome:
        conn.execute(
            "UPDATE sessions SET outcome = ? WHERE id = ?",
            (outcome, session_id),
        )

    conn.commit()
    return _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def link_session_to_thread(thread_id: str, session_id: str,
                           notes: str = None) -> dict | None:
    """Link a session to a thread."""
    conn = _db()
    row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if not row:
        return None

    # Threads in the DB don't have a sessions list like YAML did.
    # We can store the link via the sessions table using thread_id column.
    sess_exists = conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not sess_exists:
        conn.execute(
            "INSERT INTO sessions (id, status, started_at, thread_id) VALUES (?, 'active', ?, ?)",
            (session_id, _now(), thread_id),
        )
    else:
        conn.execute(
            "UPDATE sessions SET thread_id = ? WHERE id = ?",
            (thread_id, session_id),
        )

    conn.commit()

    thread = _row_to_thread(row)
    # Return with session count for CLI display
    sess_count = conn.execute(
        "SELECT count(*) as cnt FROM sessions WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    if sess_count:
        thread["sessions"] = ["s"] * sess_count["cnt"]  # Placeholder list for len()
    return thread


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
    conn = _db()
    project_id = detect_project_from_cwd(cwd)
    if not project_id:
        return []
    rows = conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? AND status IN ('active', 'todo') ORDER BY priority, created_at",
        (project_id,),
    ).fetchall()
    return [_row_to_task(r) for r in rows]


# ── Migration ─────────────────────────────────────────

def migrate_task_ids() -> dict:
    """Migrate old t1, t2, ... IDs to new project-scoped IDs.

    Returns mapping of old_id -> new_id.
    """
    conn = _db()
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    id_map = {}

    for row in rows:
        old_id = row["id"]
        # Skip if already in new format
        if "#" in old_id:
            continue

        project = row["project_id"]
        prefix = _project_prefix(project)
        new_id = _next_scoped_id(prefix)

        # Create new task with new ID
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

        # Move handoff
        conn.execute(
            "UPDATE task_handoffs SET task_id = ? WHERE task_id = ?",
            (new_id, old_id),
        )

        # Delete old
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


# ── Bulk accessors ────────────────────────────────────

def load_all() -> dict:
    """Load all work data. Use for dashboards/reviews."""
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
    conn = _db()

    # Count top-level tasks by status
    rows = conn.execute(
        "SELECT status, count(*) as cnt FROM tasks WHERE parent_id IS NULL GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in rows}
    total = sum(by_status.values())

    # Count by priority
    prows = conn.execute(
        "SELECT priority, count(*) as cnt FROM tasks WHERE parent_id IS NULL GROUP BY priority"
    ).fetchall()
    by_priority = {str(r["priority"]): r["cnt"] for r in prows}

    project_count = conn.execute("SELECT count(*) as cnt FROM projects").fetchone()["cnt"]
    goal_count = conn.execute("SELECT count(*) as cnt FROM goals").fetchone()["cnt"]
    thread_count = conn.execute("SELECT count(*) as cnt FROM threads").fetchone()["cnt"]
    inbox_count = conn.execute("SELECT count(*) as cnt FROM inbox").fetchone()["cnt"]

    return {
        "total_tasks": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "projects": project_count,
        "goals": goal_count,
        "threads": thread_count,
        "inbox": inbox_count,
    }


def _count_by(items: list, field: str) -> dict:
    counts = {}
    for item in items:
        val = str(item.get(field, "unset"))
        counts[val] = counts.get(val, 0) + 1
    return counts
