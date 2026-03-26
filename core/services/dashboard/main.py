"""Dashboard server — FastAPI on port 4096."""

import setproctitle; setproctitle.setproctitle("aos-dashboard")

import asyncio
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from activity import (
    log_activity, update_activity, get_recent, get_agent_stats,
    log_conversation as _log_conversation, update_conversation as _update_conversation,
    get_conversations, get_conversation_stats,
    upsert_session, end_session, get_sessions, get_session,
    get_session_stats, get_session_activity, should_log_to_feed,
    get_today_summary, get_recent_sessions_enriched,
)
from agent_registry import AgentRegistry

WORKSPACE = Path.home() / "aos"
registry = AgentRegistry(WORKSPACE)

app = FastAPI(title="AOS Dashboard")


def _get_version() -> str:
    """Read current AOS version from VERSION file."""
    version_file = WORKSPACE / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "dev"


def _get_changelog() -> list[dict]:
    """Parse CHANGELOG.md into structured release entries."""
    changelog_file = WORKSPACE / "CHANGELOG.md"
    if not changelog_file.exists():
        return []
    import re
    content = changelog_file.read_text()
    entries = []
    # Split by ## vX.Y.Z headers
    parts = re.split(r'\n(?=## v)', content)
    for part in parts:
        match = re.match(r'## (v[\d.]+)\s*—?\s*(.*?)\n(.*)', part, re.DOTALL)
        if match:
            version = match.group(1)
            date = match.group(2).strip()
            body = match.group(3).strip()
            # Flat format: each non-empty line is a change
            changes = [l.strip() for l in body.split('\n')
                       if l.strip() and not l.startswith('#')]
            # Group by prefix (Added/Changed/Fixed/Removed)
            grouped = {"Added": [], "Changed": [], "Fixed": [], "Removed": [], "Other": []}
            for line in changes:
                placed = False
                for prefix in ("Added", "Changed", "Fixed", "Removed"):
                    if line.startswith(prefix + ": "):
                        grouped[prefix].append(line[len(prefix)+2:])
                        placed = True
                        break
                if not placed:
                    grouped["Other"].append(line)
            entries.append({
                "version": version,
                "date": date,
                "changes": changes,
                "grouped": {k: v for k, v in grouped.items() if v},
                "total": len(changes),
            })
    return entries

# ── In-memory SSE event bus ──────────────────────────
# Each connected SSE client gets a Queue. Work mutations broadcast to all.
_sse_subscribers: list[asyncio.Queue] = []
_MAX_SSE_SUBSCRIBERS = 50


def _broadcast_work_event(event: dict):
    """Push a work event to all connected SSE clients."""
    for q in _sse_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop if client is too slow

from starlette.staticfiles import StaticFiles

# Serve static CSS/JS
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _work_mod(name: str = "engine"):
    """Import a work system module by name (engine, query, detect_projects)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, str(Path.home() / "aos" / "core" / "work" / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_yaml(path: str) -> dict:
    """Load YAML config — checks user config (~/.aos/) first, then system (~/aos/)."""
    user_path = Path.home() / ".aos" / path
    if user_path.exists():
        return yaml.safe_load(user_path.read_text()) or {}
    full = WORKSPACE / path
    if full.exists():
        return yaml.safe_load(full.read_text()) or {}
    return {}


def _system_health() -> dict:
    """Gather system health metrics."""
    # Disk
    usage = shutil.disk_usage("/")
    disk_pct = round(usage.used / usage.total * 100, 1)
    disk_free_gb = round(usage.free / (1024**3), 1)

    # RAM via vm_stat
    ram_pct = 0
    ram_used_gb = 0
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        pages = {}
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split(":")
            if len(parts) == 2:
                try:
                    pages[parts[0].strip()] = int(parts[1].strip().rstrip("."))
                except ValueError:
                    pass
        # Parse page size from vm_stat header (e.g. "page size of 16384 bytes")
        header = result.stdout.strip().split("\n")[0] if result.stdout else ""
        page_size = 16384  # fallback
        for word in header.split():
            if word.isdigit():
                page_size = int(word)
                break
        active = pages.get("Pages active", 0) * page_size
        wired = pages.get("Pages wired down", 0) * page_size
        free = pages.get("Pages free", 0) * page_size
        inactive = pages.get("Pages inactive", 0) * page_size
        total = free + active + inactive + wired
        used = active + wired
        ram_pct = round(used / total * 100, 1) if total else 0
        ram_used_gb = round(used / (1024**3), 1)
    except Exception:
        pass

    return {
        "disk_pct": disk_pct,
        "disk_free_gb": disk_free_gb,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used_gb,
    }


def _service_status() -> dict:
    """Check which services are running."""
    services = {}

    # Listen server
    try:
        r = httpx.get("http://localhost:7600/jobs", timeout=3)
        if r.status_code == 200:
            # Listen may return JSON or YAML — just check it responds
            try:
                jobs = r.json()
                active = sum(1 for j in jobs if j.get("status") == "running")
                services["listen"] = {"status": "online", "detail": f"{len(jobs)} jobs ({active} active)"}
            except Exception:
                # Response is valid but not JSON (e.g. YAML) — still online
                services["listen"] = {"status": "online", "detail": "Running"}
        else:
            services["listen"] = {"status": "offline", "detail": f"HTTP {r.status_code}"}
    except Exception:
        services["listen"] = {"status": "offline", "detail": "Not responding"}

    # Bridge
    try:
        result = subprocess.run(["pgrep", "-f", "services/bridge/main.py"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            services["bridge"] = {"status": "online", "detail": "Telegram + Slack"}
        else:
            services["bridge"] = {"status": "offline", "detail": "Not running"}
    except Exception:
        services["bridge"] = {"status": "offline", "detail": "Check failed"}

    # Memory MCP
    try:
        result = subprocess.run(["pgrep", "-f", "services/memory/main.py"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            services["memory"] = {"status": "online", "detail": "MCP server"}
        else:
            services["memory"] = {"status": "offline", "detail": "Starts on demand"}
    except Exception:
        services["memory"] = {"status": "offline", "detail": "Check failed"}

    return services


def _get_attention_items(services: dict, cron_data: dict, tasks: list[dict], health: dict) -> list[dict]:
    """Build attention items — things that need the operator's attention right now."""
    items = []

    # Services down (skip on-demand services like memory MCP)
    on_demand = {"memory"}
    for name, svc in services.items():
        if svc["status"] != "online" and name not in on_demand:
            items.append({"type": "error", "icon": "alert-triangle", "text": f"{name.capitalize()} is offline", "detail": svc.get("detail", ""), "link": "/crons"})

    # Failed crons
    for job in cron_data.get("jobs", []):
        if job["status"] == "failed":
            items.append({"type": "error", "icon": "x-circle", "text": f"Cron failed: {job['name']}", "detail": f"Exit code {job.get('exit_code', '?')}", "link": "/crons"})
        elif job["status"] == "stale":
            items.append({"type": "warning", "icon": "clock", "text": f"Cron stale: {job['name']}", "detail": "Hasn't run on schedule", "link": "/crons"})

    # Disk pressure
    if health.get("disk_pct", 0) > 85:
        items.append({"type": "warning", "icon": "hard-drive", "text": f"Disk at {health['disk_pct']}%", "detail": f"{health.get('disk_free_gb', '?')}GB free"})

    # Stale tasks (active but no progress for 3+ days)
    stale_path = Path.home() / ".aos" / "work" / "stale-report.yaml"
    if stale_path.exists():
        try:
            stale = yaml.safe_load(stale_path.read_text()) or {}
            for t in stale.get("stale_tasks", []):
                items.append({"type": "warning", "icon": "pause-circle", "text": f"Stale: {t.get('title', t.get('id', '?'))}", "detail": t.get("reason", ""), "link": "/work"})
            for g in stale.get("orphan_goals", []):
                items.append({"type": "warning", "icon": "target", "text": f"Orphan goal: {g.get('title', g.get('id', '?'))}", "detail": g.get("reason", ""), "link": "/work"})
        except Exception:
            pass

    # Sort: errors first, then warnings
    items.sort(key=lambda i: 0 if i["type"] == "error" else 1)
    return items


def _load_morning_context() -> dict:
    """Load morning context (weather, prayer times, focus)."""
    path = Path.home() / ".aos" / "work" / "morning-context.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _load_weekly_metrics() -> dict:
    """Load current week's metrics for momentum display."""
    import glob as glob_mod
    metrics_dir = Path.home() / ".aos" / "work" / "metrics"
    if not metrics_dir.exists():
        return {}
    files = sorted(metrics_dir.glob("*.yaml"), reverse=True)
    if not files:
        return {}
    try:
        return yaml.safe_load(files[0].read_text()) or {}
    except Exception:
        return {}


def _load_agents() -> list[dict]:
    """Load agent definitions from .claude/agents/*.md frontmatter."""
    agents = registry.list_agents()
    # Add 'arabic' alias for backward compat with templates
    for a in agents:
        a["arabic"] = a.get("arabic_name", "")
    return agents


def _load_tasks() -> list[dict]:
    """Load tasks from v2 work engine."""
    try:
        mod = _work_mod("engine")
        tasks = mod.get_all_tasks()
        # Only return active tasks (not done/cancelled), top-level only
        return [t for t in tasks if t.get("status") not in ("done", "cancelled") and not t.get("parent")]
    except Exception:
        return []


def _load_work_activity(limit: int = 20) -> list[dict]:
    """Load recent work activity events."""
    try:
        mod = _work_mod("engine")
        return mod.get_activity(limit)
    except Exception:
        return []


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    loop = asyncio.get_event_loop()
    health, services = await asyncio.gather(
        loop.run_in_executor(None, _system_health),
        loop.run_in_executor(None, _service_status),
    )
    tasks = _load_tasks()
    work_activity = _load_work_activity(20)
    activity = get_recent(30)
    today_summary = get_today_summary()

    # Overall system health status
    online_count = sum(1 for s in services.values() if s["status"] == "online")
    total_svc = len(services)
    if online_count == total_svc:
        health_status = "ok"
        health_text = "All systems operational"
    elif online_count == 0:
        health_status = "error"
        offline = [n for n, s in services.items() if s["status"] != "online"]
        health_text = f"{', '.join(n.capitalize() for n in offline)} offline"
    else:
        health_status = "warning"
        offline = [n for n, s in services.items() if s["status"] != "online"]
        health_text = f"{', '.join(n.capitalize() for n in offline)} offline"

    # Current time in configured timezone
    goals = _load_yaml("config/goals.yaml")
    tz_name = goals.get("work_hours", {}).get("timezone", "America/Toronto")
    now = datetime.now(ZoneInfo(tz_name))

    # Automations — unified from scheduler
    cron_data = _get_scheduler_crons()
    launch_agents = _get_launch_agents()

    # Attention items
    attention = _get_attention_items(services, cron_data, tasks, health)

    # Morning context
    morning = _load_morning_context()

    # Weekly metrics for momentum
    weekly = _load_weekly_metrics()

    # Task count for sidebar badge
    active_task_count = sum(1 for t in tasks if t.get("status") in ("active", "todo"))

    return templates.TemplateResponse(request, "dashboard.html", {
        "active_page": "dashboard",
        "task_count": active_task_count if active_task_count else None,
        "health": health,
        "tasks": tasks,
        "activity": activity,
        "today": today_summary,
        "work_activity": work_activity,
        "health_status": health_status,
        "health_text": health_text,
        "now": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "cron_data": cron_data,
        "launch_agents": launch_agents,
        "attention": attention,
        "morning": morning,
        "weekly": weekly,
        "version": _get_version(),
    })


# --- API endpoints for HTMX partial updates ---

@app.get("/api/health")
async def api_health():
    return await asyncio.get_event_loop().run_in_executor(None, _system_health)


@app.get("/api/version")
async def api_version():
    return {"version": _get_version()}


@app.get("/api/changelog")
async def api_changelog():
    return _get_changelog()


@app.get("/api/services")
async def api_services():
    return await asyncio.get_event_loop().run_in_executor(None, _service_status)


@app.get("/api/activity")
async def api_activity(limit: int = 30):
    return get_recent(limit)


@app.get("/api/work/activity")
async def api_work_activity(limit: int = 30):
    """Recent work system events (task created, completed, handoff, etc.)."""
    return _load_work_activity(limit)


@app.get("/api/work")
async def api_work():
    """Return v2 work system data."""
    try:
        mod = _work_mod("engine")
        data = mod.load_all()
        summary = mod.summary()
        return {"tasks": data["tasks"], "projects": data["projects"], "goals": data["goals"], "threads": data["threads"], "inbox": data["inbox"], "summary": summary}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/work/notify")
async def api_work_notify(request: Request):
    """Receive work event from CLI engine and broadcast to SSE clients."""
    event = await request.json()
    _broadcast_work_event(event)
    return {"ok": True}


@app.post("/api/tasks")
async def api_create_task(request: Request):
    """Create a new task."""
    body = await request.json()
    mod = _work_mod("engine")
    task = mod.add_task(
        title=body.get("title", "Untitled"),
        priority=body.get("priority", 3),
        project=body.get("project"),
        status=body.get("status", "todo"),
        parent=body.get("parent"),
    )
    _broadcast_work_event({"action": "task_created", "task_id": task.get("id"), "title": task.get("title"), "project": task.get("project")})
    return task

@app.patch("/api/tasks/{task_id}")
async def api_update_task(task_id: str, request: Request):
    """Update a task (status, title, priority, project, etc.)."""
    body = await request.json()
    mod = _work_mod("engine")

    # Handle special status transitions
    status = body.pop("status", None)
    if status == "done":
        mod.complete_task(task_id)
    elif status == "active":
        mod.start_task(task_id)
    elif status == "cancelled":
        mod.cancel_task(task_id)
    elif status:
        body["status"] = status

    if body:
        result = mod.update_task(task_id, **body)
    else:
        result = mod.get_task(task_id)

    action = f"task_{status}" if status else "task_updated"
    _broadcast_work_event({"action": action, "task_id": task_id, "title": (result or {}).get("title"), "project": (result or {}).get("project")})
    return result or {"error": "Task not found"}

@app.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str):
    mod = _work_mod("engine")
    ok = mod.delete_task(task_id)
    return {"ok": ok}

@app.post("/api/projects")
async def api_create_project(request: Request):
    body = await request.json()
    mod = _work_mod("engine")
    project = mod.add_project(
        title=body.get("title", "Untitled"),
        goal=body.get("goal"),
        done_when=body.get("done_when"),
    )
    return project

@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, request: Request):
    body = await request.json()
    mod = _work_mod("engine")
    result = mod.update_project(project_id, **body)
    return result or {"error": "Project not found"}

@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    mod = _work_mod("engine")
    ok = mod.delete_project(project_id)
    return {"ok": ok}

@app.post("/api/goals")
async def api_create_goal(request: Request):
    body = await request.json()
    mod = _work_mod("engine")
    goal = mod.add_goal(
        title=body.get("title", "Untitled"),
        weight=body.get("weight"),
    )
    return goal

@app.post("/api/tasks/{task_id}/subtasks")
async def api_create_subtask(task_id: str, request: Request):
    """Create a subtask under an existing task."""
    body = await request.json()
    mod = _work_mod("engine")
    sub = mod.add_subtask(
        parent_id=task_id,
        title=body.get("title", "Untitled"),
        priority=body.get("priority"),
        status=body.get("status", "todo"),
    )
    return sub or {"error": "Parent task not found"}

@app.put("/api/tasks/{task_id}/handoff")
async def api_write_handoff(task_id: str, request: Request):
    """Write handoff context for a task."""
    body = await request.json()
    mod = _work_mod("engine")
    result = mod.write_handoff(
        task_id=task_id,
        state=body.get("state", ""),
        next_step=body.get("next_step"),
        files_touched=body.get("files_touched"),
        decisions=body.get("decisions"),
        blockers=body.get("blockers"),
    )
    return result or {"error": "Task not found"}

@app.get("/api/tasks/{task_id}/dispatch")
async def api_dispatch_prompt(task_id: str):
    """Get a dispatch prompt for a task (for agent handoff injection)."""
    mod = _work_mod("engine")
    prompt = mod.build_handoff_prompt(task_id)
    return {"prompt": prompt} if prompt else {"error": "Task not found"}

@app.post("/api/inbox")
async def api_create_inbox(request: Request):
    body = await request.json()
    mod = _work_mod("engine")
    item = mod.add_inbox(text=body.get("text", ""))
    return item

@app.delete("/api/inbox/{inbox_id}")
async def api_delete_inbox(inbox_id: str):
    mod = _work_mod("engine")
    ok = mod.delete_inbox(inbox_id)
    return {"ok": ok}


@app.get("/api/messages/unanswered")
async def api_messages_unanswered():
    """Return unanswered messages from triage state."""
    triage_file = Path.home() / ".aos" / "work" / "triage-state.json"
    try:
        if triage_file.exists():
            state = json.loads(triage_file.read_text())
            unanswered = state.get("unanswered", {})
            # Add relative time to each entry
            now = datetime.now()
            entries = []
            for key, entry in unanswered.items():
                entry_copy = dict(entry)
                entry_copy["key"] = key
                try:
                    ts = entry.get("received_at", "")
                    if ts:
                        msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if msg_dt.tzinfo:
                            from zoneinfo import ZoneInfo
                            now_aware = datetime.now(msg_dt.tzinfo)
                            delta = int((now_aware - msg_dt).total_seconds())
                        else:
                            delta = int((now - msg_dt).total_seconds())
                        if delta < 60:
                            entry_copy["time_ago"] = "just now"
                        elif delta < 3600:
                            entry_copy["time_ago"] = f"{delta // 60}m ago"
                        elif delta < 86400:
                            entry_copy["time_ago"] = f"{delta // 3600}h ago"
                        else:
                            entry_copy["time_ago"] = f"{delta // 86400}d ago"
                    else:
                        entry_copy["time_ago"] = "recently"
                except Exception:
                    entry_copy["time_ago"] = "recently"
                entries.append(entry_copy)
            # Sort oldest first
            entries.sort(key=lambda e: e.get("received_at", ""))
            return {"unanswered": entries, "count": len(entries)}
        return {"unanswered": [], "count": 0}
    except Exception as e:
        return {"unanswered": [], "count": 0, "error": str(e)}


@app.get("/api/detected-projects")
async def api_detected_projects():
    """Auto-detect projects from session patterns and directories."""
    try:
        mod = _work_mod("detect_projects")
        return mod.detect()
    except Exception as e:
        return {"error": str(e)}


@app.get("/work", response_class=HTMLResponse)
async def work_page(request: Request):
    """Work page — tasks, projects, goals, threads with subtask trees and handoffs."""
    try:
        mod = _work_mod("engine")

        q_mod = _work_mod("query")

        data = mod.load_all()
        summary_data = mod.summary()
    except Exception:
        data = {"tasks": [], "projects": [], "goals": [], "threads": [], "inbox": []}
        summary_data = {}
        q_mod = None

    tasks = data.get("tasks", [])
    projects_raw = data.get("projects", [])
    goals = data.get("goals", [])
    threads = data.get("threads", [])
    inbox = data.get("inbox", [])

    # Build task trees (attach subtasks to parents)
    if q_mod:
        task_trees = q_mod.build_task_trees(tasks)
    else:
        task_trees = [t for t in tasks if not t.get("parent")]
        for t in task_trees:
            t["subtasks"] = [s for s in tasks if s.get("parent") == t["id"]]

    # Group top-level tasks by project
    project_map = {p["id"]: {**p, "tasks": [], "task_count": 0, "progress": {"done": 0, "total": 0, "pct": 0}} for p in projects_raw}
    unassigned = []

    for task in task_trees:
        proj_id = task.get("project")
        if proj_id and proj_id in project_map:
            project_map[proj_id]["tasks"].append(task)
            project_map[proj_id]["task_count"] += 1
        elif proj_id is None:
            unassigned.append(task)

    # Compute project progress
    if q_mod:
        for pid, pdata in project_map.items():
            pdata["progress"] = q_mod.project_progress(pid, tasks)

    # Sort tasks within projects: active first, then todo, then done
    status_order = {"active": 0, "todo": 1, "done": 2, "cancelled": 3}
    for p in project_map.values():
        p["tasks"].sort(key=lambda t: (status_order.get(t.get("status", "todo"), 1), t.get("priority", 3)))

    unassigned.sort(key=lambda t: (status_order.get(t.get("status", "todo"), 1), t.get("priority", 3)))

    # Summary counts (top-level only)
    top_level = [t for t in tasks if not t.get("parent")]
    summary = {
        "active": sum(1 for t in top_level if t.get("status") == "active"),
        "todo": sum(1 for t in top_level if t.get("status") == "todo"),
        "done": sum(1 for t in top_level if t.get("status") == "done"),
        "total": len(top_level),
        "with_handoffs": sum(1 for t in top_level if t.get("handoff")),
    }

    # Count active tasks for sidebar badge
    active_count = summary["active"] + summary["todo"]

    # Auto-detect untracked projects
    detected_projects = []
    try:
        det_mod = _work_mod("detect_projects")
        detected_projects = det_mod.detect()
    except Exception:
        pass

    return templates.TemplateResponse(request, "work.html", {
        "active_page": "work",
        "task_count": active_count if active_count else None,
        "projects": list(project_map.values()),
        "unassigned_tasks": unassigned,
        "goals": goals,
        "threads": threads,
        "inbox": inbox,
        "summary": summary,
        "detected_projects": detected_projects,
        "all_tasks": task_trees,
    })


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    agents = _load_agents()
    agent_stats = get_agent_stats()
    global_agents = [a for a in agents if a.get("scope") == "global"]
    project_groups = {}
    for a in agents:
        if a.get("scope") == "project" and a.get("project"):
            project_groups.setdefault(a["project"], []).append(a)
    return templates.TemplateResponse(request, "agents.html", {
        "active_page": "agents",
        "global_agents": global_agents,
        "project_groups": project_groups,
        "all_agents": agents,
        "agent_stats": {s["agent"]: s for s in agent_stats},
    })


@app.get("/api/agents")
async def api_agents():
    return _load_agents()


@app.get("/api/agents/{name}")
async def api_agent_detail(name: str):
    agent = registry.get_agent(name)
    if not agent:
        return {"error": "not found"}
    # Include recent activity
    from activity import get_recent as _get_recent
    all_activity = _get_recent(100)
    agent["recent_activity"] = [a for a in all_activity if a["agent"] == name][:20]
    return agent


@app.post("/api/agents")
async def api_create_agent(request: Request):
    body = await request.json()
    try:
        agent = registry.create_agent(
            name=body["name"],
            role=body.get("role", "Agent"),
            description=body.get("description", ""),
            arabic_name=body.get("arabic_name", ""),
            color=body.get("color", ""),
            scope=body.get("scope", "global"),
            project=body.get("project", ""),
            model=body.get("model", "sonnet"),
            tools=body.get("tools"),
        )
        return agent
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/activity")
async def api_log_activity(agent: str, action: str, parent_agent: str = None,
                           status: str = "completed", summary: str = None):
    aid = log_activity(agent, action, parent_agent, status, summary)
    return {"id": aid}


@app.get("/api/stream")
async def api_stream(request: Request):
    """SSE endpoint — pushes new activity, health, services, and work events."""
    async def event_generator():
        last_id = 0
        # Get current max ID
        recent = get_recent(1)
        if recent:
            last_id = recent[0]["id"]

        # Subscribe to work event bus (bounded to prevent memory exhaustion)
        if len(_sse_subscribers) >= _MAX_SSE_SUBSCRIBERS:
            yield f"event: error\ndata: {{\"message\": \"too many connections\"}}\n\n"
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        _sse_subscribers.append(queue)

        try:
            tick = 0
            while True:
                if await request.is_disconnected():
                    break

                # Check for new activity every 2 seconds
                recent = get_recent(10)
                new_items = [r for r in recent if r["id"] > last_id]
                if new_items:
                    last_id = max(r["id"] for r in new_items)
                    for item in reversed(new_items):
                        yield f"event: activity\ndata: {json.dumps(item)}\n\n"

                # Drain work events from the bus (instant push)
                while not queue.empty():
                    try:
                        event = queue.get_nowait()
                        yield f"event: work\ndata: {json.dumps(event)}\n\n"
                    except asyncio.QueueEmpty:
                        break

                # Send health + services every 15 seconds
                if tick % 15 == 0:
                    health = _system_health()
                    yield f"event: health\ndata: {json.dumps(health)}\n\n"
                    services = _service_status()
                    yield f"event: services\ndata: {json.dumps(services)}\n\n"

                tick += 2
                await asyncio.sleep(2)
        finally:
            _sse_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.patch("/api/activity/{activity_id}")
async def api_update_activity(activity_id: int, status: str,
                              summary: str = None, duration_ms: int = None):
    update_activity(activity_id, status, summary, duration_ms)
    return {"ok": True}


# --- Conversations ---

@app.post("/api/conversations")
async def api_log_conversation(request: Request):
    body = await request.json()
    cid = _log_conversation(
        channel=body.get("channel", "telegram"),
        user_key=body.get("user_key", ""),
        agent=body.get("agent"),
        topic_name=body.get("topic_name"),
        message=body.get("message", ""),
        response=body.get("response"),
        duration_ms=body.get("duration_ms"),
        message_type=body.get("message_type", "text"),
    )
    return {"id": cid}


@app.patch("/api/conversations/{conv_id}")
async def api_update_conversation(conv_id: int, request: Request):
    body = await request.json()
    _update_conversation(conv_id, response=body.get("response", ""),
                         duration_ms=body.get("duration_ms"))
    return {"ok": True}


@app.get("/api/conversations")
async def api_get_conversations(limit: int = 50, agent: str = None):
    return get_conversations(limit=limit, agent=agent)


@app.get("/conversations")
async def conversations_page():
    """Redirect to unified history page, filtered to messages."""
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/history?channel=telegram", status_code=302)


# --- History (unified sessions + conversations) ---


def _get_history(limit: int = 100, channel: str = None, query: str = None) -> list[dict]:
    """Merge sessions and conversations into one chronological timeline."""
    from activity import _get_db
    conn = _get_db()
    try:
        return _get_history_inner(conn, limit, channel, query)
    finally:
        conn.close()


def _get_history_inner(conn, limit: int, channel: str, query: str) -> list[dict]:
    items = []

    # Load sessions
    if channel in (None, "all", "cli"):
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit * 2,)
        ).fetchall()
        for r in rows:
            d = dict(r)
            files = json.loads(d["files_modified"]) if isinstance(d["files_modified"], str) else (d["files_modified"] or [])
            tools = json.loads(d["tools_used"]) if isinstance(d["tools_used"], str) else (d["tools_used"] or {})
            # Build summary text for search
            file_names = [f.split("/")[-1] for f in files] if files else []
            tool_names = list(tools.keys()) if tools else []
            summary = " ".join(file_names + tool_names + [d.get("project") or ""])

            items.append({
                "type": "session",
                "channel": "cli",
                "ts": d["started_at"] or "",
                "session_id": d["session_id"],
                "project": d.get("project") or (d["working_dir"].split("/")[-1] if d.get("working_dir") else None),
                "status": d["status"],
                "total_tools": d["total_tools"] or 0,
                "files": file_names,
                "tools": tools,
                "duration_s": _iso_duration_seconds(d["started_at"], d["ended_at"]),
                "summary": summary,
                "working_dir": d.get("working_dir"),
            })

    # Load conversations
    if channel in (None, "all", "telegram", "slack"):
        where = ""
        params = [limit * 2]
        if channel and channel not in ("all",):
            where = "WHERE channel = ?"
            params = [channel, limit * 2]
        rows = conn.execute(
            f"SELECT * FROM conversations {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()
        for r in rows:
            d = dict(r)
            msg = d.get("message", "") or ""
            resp = d.get("response", "") or ""
            items.append({
                "type": "conversation",
                "channel": d.get("channel", "telegram"),
                "ts": d["timestamp"] or "",
                "message": msg[:200],
                "response": resp[:300],
                "full_message": msg,
                "full_response": resp,
                "topic": d.get("topic_name"),
                "duration_ms": d.get("duration_ms"),
                "agent": d.get("agent"),
                "conv_id": d["id"],
                "summary": f"{msg} {resp}",
            })

    # Filter by search query
    if query:
        q = query.lower()
        items = [i for i in items if q in (i.get("summary") or "").lower()
                 or q in (i.get("message") or "").lower()
                 or q in (i.get("response") or "").lower()
                 or q in (i.get("project") or "").lower()]

    # Sort by timestamp descending
    items.sort(key=lambda i: i.get("ts", ""), reverse=True)
    return items[:limit]


def _iso_duration_seconds(start: str, end: str) -> int | None:
    """Compute duration in seconds between two ISO timestamps."""
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return max(0, int((e - s).total_seconds()))
    except Exception:
        return None


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, channel: str = None, q: str = None):
    items = _get_history(limit=150, channel=channel, query=q)
    # Stats
    from activity import _db
    with _db() as conn:
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    return templates.TemplateResponse(request, "history.html", {
        "active_page": "history",
        "items": items,
        "session_count": session_count,
        "conv_count": conv_count,
        "total_count": session_count + conv_count,
        "current_channel": channel or "all",
        "current_query": q or "",
    })


@app.get("/api/history")
async def api_history(limit: int = 100, channel: str = None, q: str = None):
    return _get_history(limit=limit, channel=channel, query=q)


# --- Sessions ---

def _duration_filter(start: str, end: str) -> str:
    """Jinja filter: compute human-readable duration between two ISO timestamps."""
    if not start or not end:
        return ""
    try:
        from datetime import datetime, timezone
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        delta = (e - s).total_seconds()
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta // 60)}m {int(delta % 60)}s"
        return f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m"
    except Exception:
        return ""

# Register the filter in Jinja
templates.env.globals["duration"] = _duration_filter
templates.env.filters["replace_home"] = lambda path: path.replace(str(Path.home()) + "/", "~/") if path else ""


@app.get("/sessions")
async def sessions_page():
    """Redirect to unified history page, filtered to CLI sessions."""
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/history?channel=cli", status_code=302)


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail_page(request: Request, session_id: str):
    s = get_session(session_id)
    if not s:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    activity = get_session_activity(session_id, limit=200)
    return templates.TemplateResponse(request, "session_detail.html", {
        "active_page": "history",
        "session": s,
        "activity": activity,
    })


@app.get("/api/sessions")
async def api_sessions(limit: int = 50, status: str = None, agent: str = None):
    return get_sessions(limit=limit, status=status, agent=agent)


@app.get("/api/sessions/{session_id}")
async def api_session_detail(session_id: str):
    s = get_session(session_id)
    if not s:
        return {"error": "not found"}
    return s


@app.get("/api/sessions/{session_id}/activity")
async def api_session_activity(session_id: str, limit: int = 100):
    return get_session_activity(session_id, limit)


@app.post("/api/sessions/hook")
async def api_session_hook(request: Request):
    """Receive hook events from Claude Code's PostToolUse and Stop hooks."""
    body = await request.json()
    hook_type = body.get("hook_type", "")
    payload = body.get("payload", {})

    session_id = payload.get("session_id", "")
    if not session_id:
        return {"ok": False, "error": "no session_id"}

    def _shorten(path: str) -> str:
        return path.replace(str(Path.home()) + "/", "~/") if path else ""

    def _filename(path: str) -> str:
        """Get just the filename from a path."""
        return path.rsplit("/", 1)[-1] if "/" in path else path

    if hook_type == "stop":
        end_session(session_id)
        s = get_session(session_id)
        if s:
            files = s.get("files_modified", [])
            file_count = len(files)
            total = s.get("total_tools", 0)
            # Build a useful summary
            if file_count > 0:
                names = [_filename(f) for f in files[:3]]
                file_str = ", ".join(names)
                if file_count > 3:
                    file_str += f" +{file_count - 3} more"
                summary = f"Edited {file_str} ({total} ops)"
            else:
                summary = f"Completed ({total} ops)"
            log_activity("claude", "Session ended", status="completed",
                         summary=summary, session_id=session_id)
        return {"ok": True}

    if hook_type == "tool":
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})
        working_dir = payload.get("cwd", "")

        is_new = upsert_session(session_id, tool_name, tool_input, working_dir)

        if is_new:
            dir_short = _shorten(working_dir)
            # Extract project name from path
            project = dir_short.split("/")[-1] if dir_short else "unknown"
            log_activity("claude", "Session started", status="running",
                         summary=f"Working in {project}", session_id=session_id)

        # Log interesting tool uses to activity feed
        if should_log_to_feed(tool_name, tool_input):
            if tool_name in ("Write", "Edit"):
                fpath = tool_input.get("file_path", "")
                fname = _filename(fpath)
                action = "Created" if tool_name == "Write" else "Edited"
                log_activity("claude", f"{action} {fname}",
                             status="completed",
                             summary=_shorten(fpath),
                             session_id=session_id)
            elif tool_name == "Bash":
                cmd = tool_input.get("command", "").strip()
                # Make bash commands human-readable
                if cmd.startswith("git commit"):
                    action = "Git commit"
                elif cmd.startswith("git push"):
                    action = "Git push"
                elif "install" in cmd:
                    action = "Installing packages"
                elif cmd.startswith("docker") or cmd.startswith("launchctl"):
                    action = "Service management"
                else:
                    action = "Ran command"
                log_activity("claude", action, status="completed",
                             summary=cmd[:100], session_id=session_id)
            elif tool_name == "Agent":
                desc = tool_input.get("description", "")[:80]
                log_activity("claude", f"Launched agent",
                             status="completed",
                             summary=desc, session_id=session_id)

        return {"ok": True}

    return {"ok": False, "error": f"unknown hook_type: {hook_type}"}


# --- Automations ---


def _get_launch_agents() -> list[dict]:
    """Get status of AOS LaunchAgents (com.aos.* and legacy com.agent.*)."""
    agents = []
    la_dir = Path.home() / "Library" / "LaunchAgents"
    seen = set()
    for plist in sorted(list(la_dir.glob("com.aos.*.plist")) + list(la_dir.glob("com.agent.*.plist"))):
        label = plist.stem
        short_name = label.replace("com.aos.", "").replace("com.agent.", "")
        if short_name in seen:
            continue
        seen.add(short_name)
        # Check if loaded and get PID
        status = "unknown"
        pid = None
        try:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                status = "running"
                for line in result.stdout.split("\n"):
                    if '"PID"' in line or "PID" in line:
                        import re
                        m = re.search(r"(\d+)", line.split("=")[-1] if "=" in line else line)
                        if m:
                            pid = int(m.group(1))
            else:
                status = "stopped"
        except Exception:
            status = "error"
        # Get log paths from plist
        log_out = None
        log_err = None
        try:
            import plistlib
            with open(plist, "rb") as f:
                pdata = plistlib.load(f)
            log_out = pdata.get("StandardOutPath")
            log_err = pdata.get("StandardErrorPath")
        except Exception:
            pass
        agents.append({
            "name": short_name,
            "type": "launchagent",
            "label": label,
            "status": status,
            "pid": pid,
            "log_out": log_out,
            "log_err": log_err,
        })
    return agents


@app.get("/api/automations")
async def api_automations():
    return {
        "launch_agents": _get_launch_agents(),
    }


@app.post("/api/automations/cron/{name}/run")
async def api_run_cron(name: str):
    """Trigger a scheduled job immediately via the Listen job server."""
    config_path = Path.home() / "aos" / "config" / "crons.yaml"
    command = None
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
        job_def = raw.get("jobs", {}).get(name)
        if job_def:
            command = job_def.get("command")
    except Exception:
        pass
    if not command:
        return {"error": "not found"}
    try:
        r = httpx.post(
            "http://localhost:7600/jobs",
            json={"command": command, "name": f"manual:{name}"},
            timeout=5,
        )
        return {"ok": True, "job": r.json()}
    except Exception as e:
        return {"error": str(e)}


def _get_scheduler_crons() -> dict:
    """Read merged cron data: crons.yaml definitions + status.json runtime info."""
    # Paths in v2 layout
    status_path = Path.home() / ".aos" / "logs" / "crons" / "status.json"
    config_path = Path.home() / "aos" / "config" / "crons.yaml"

    # Load runtime status
    runtime: dict = {}
    if status_path.exists():
        try:
            runtime = json.loads(status_path.read_text()) or {}
        except Exception:
            pass

    # Load job definitions
    definitions: dict = {}
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text()) or {}
            definitions = raw.get("jobs", {})
        except Exception:
            pass

    def _interval_seconds(job_def: dict) -> int | None:
        """Convert 'every' field to seconds."""
        every = job_def.get("every", "")
        if not every:
            return None
        every = str(every).strip()
        if every.endswith("m"):
            return int(every[:-1]) * 60
        if every.endswith("h"):
            return int(every[:-1]) * 3600
        return None

    def _schedule_human(job_def: dict) -> str:
        every = job_def.get("every", "")
        at = job_def.get("at", "")
        weekday = job_def.get("weekday", "")
        monthday = job_def.get("monthday", "")
        if every:
            every = str(every)
            hours_str = job_def.get("active_hours", "")
            suffix = f" ({hours_str})" if hours_str else ""
            if every.endswith("m"):
                return f"Every {every[:-1]}m{suffix}"
            if every.endswith("h"):
                return f"Every {every[:-1]}h{suffix}"
            return f"Every {every}{suffix}"
        if at:
            if weekday:
                return f"{weekday.capitalize()} at {at}"
            if monthday:
                return f"Monthly on day {monthday} at {at}"
            return f"Daily at {at}"
        return "—"

    now = datetime.now()
    jobs = []
    for name, defn in definitions.items():
        enabled = defn.get("enabled", True)
        schedule = _schedule_human(defn)
        status_entry = runtime.get(name, {})
        last_run_str = status_entry.get("last_run")
        exit_code = status_entry.get("exit_code")
        duration_s = status_entry.get("duration_s", 0)
        run_count = status_entry.get("run_count", 0)
        last_failure = status_entry.get("last_failure")

        # Determine status
        if not enabled:
            status = "disabled"
        elif exit_code is not None and exit_code != 0:
            status = "failed"
        elif last_run_str:
            # Check staleness for interval jobs
            interval = _interval_seconds(defn)
            if interval:
                try:
                    last_run_dt = datetime.fromisoformat(last_run_str)
                    elapsed = (now - last_run_dt).total_seconds()
                    if elapsed > interval * 2:
                        status = "stale"
                    else:
                        status = "ok"
                except Exception:
                    status = "ok"
            else:
                status = "ok"
        elif enabled:
            status = "pending"
        else:
            status = "disabled"

        jobs.append({
            "name": name,
            "schedule": schedule,
            "enabled": enabled,
            "last_run": last_run_str,
            "exit_code": exit_code,
            "duration_s": duration_s,
            "run_count": run_count,
            "last_failure": last_failure,
            "status": status,
        })

    # Sort: enabled first (by status priority), then disabled
    priority = {"failed": 0, "stale": 1, "ok": 2, "pending": 3, "disabled": 4}
    jobs.sort(key=lambda j: priority.get(j["status"], 5))

    total = len(jobs)
    enabled_count = sum(1 for j in jobs if j["enabled"])
    ok_count = sum(1 for j in jobs if j["status"] == "ok")
    failed_count = sum(1 for j in jobs if j["status"] == "failed")
    stale_count = sum(1 for j in jobs if j["status"] == "stale")
    disabled_count = sum(1 for j in jobs if j["status"] == "disabled")

    return {
        "jobs": jobs,
        "summary": {
            "total": total,
            "enabled": enabled_count,
            "ok": ok_count,
            "failed": failed_count,
            "stale": stale_count,
            "disabled": disabled_count,
        },
    }


@app.get("/api/crons")
async def api_crons():
    """Return structured cron job data merged from scheduler status + config."""
    return _get_scheduler_crons()


@app.get("/crons", response_class=HTMLResponse)
async def crons_page(request: Request):
    return templates.TemplateResponse(request, "crons.html", {
        "active_page": "crons",
    })


@app.post("/api/automations/launchagent/{name}/restart")
async def api_restart_launchagent(name: str):
    """Restart a LaunchAgent via launchctl kickstart."""
    import os
    uid = os.getuid()
    # Try com.aos first, fall back to com.agent (legacy)
    for prefix in ("com.aos", "com.agent"):
        label = f"{prefix}.{name}"
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return {"ok": True, "output": result.stdout + result.stderr}
        except Exception:
            continue
    return {"error": f"Could not restart {name}"}


# --- Logs page ---

LOG_DIR = Path.home() / ".aos" / "logs"

# Each source merges stderr + stdout into one view
LOG_SOURCES = {
    "bridge": [LOG_DIR / "bridge.err.log", LOG_DIR / "bridge.out.log"],
    "dashboard": [LOG_DIR / "dashboard.err.log", LOG_DIR / "dashboard.out.log"],
    "listen": [LOG_DIR / "listen.err.log", LOG_DIR / "listen.out.log"],
}


def _tail_lines(path: Path, n: int = 200) -> list[str]:
    """Read last n lines from a file efficiently."""
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 65536)
            f.seek(max(0, size - chunk))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-n:]
    except Exception:
        return []


def _tail_merged(paths: list[Path], n: int = 300) -> list[str]:
    """Read last n lines from multiple files, merged and sorted by timestamp."""
    all_lines = []
    for p in paths:
        all_lines.extend(_tail_lines(p, n))
    # Sort by timestamp if present, otherwise keep order
    def sort_key(line):
        # Match "2026-03-13 18:30:37" or "INFO: ..." (no timestamp — sort last)
        import re
        m = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
        return m.group(1) if m else "9999"
    all_lines.sort(key=sort_key)
    return all_lines[-n:]


@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    return templates.TemplateResponse(request, "docs.html", {
        "active_page": "docs",
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    available = {}
    for name, paths in LOG_SOURCES.items():
        available[name] = any(p.exists() for p in paths)
    return templates.TemplateResponse(request, "logs.html", {
        "active_page": "logs",
        "log_sources": available,
    })


@app.get("/api/logs/history")
async def api_log_history(source: str = "bridge", lines: int = 300):
    paths = LOG_SOURCES.get(source)
    if not paths:
        return {"error": "unknown source", "lines": []}
    return {"lines": _tail_merged(paths, lines)}


@app.get("/api/logs/stream")
async def api_log_stream(request: Request, source: str = "bridge"):
    """SSE endpoint — tails multiple log files and pushes new lines."""
    paths = LOG_SOURCES.get(source)
    if not paths:
        async def err():
            yield f"event: error\ndata: unknown source '{source}'\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    async def tail_generator():
        # Track position for each file
        positions = {}
        for p in paths:
            positions[p] = p.stat().st_size if p.exists() else 0

        while True:
            if await request.is_disconnected():
                break

            for p in paths:
                if not p.exists():
                    continue
                current_size = p.stat().st_size
                last_pos = positions.get(p, 0)
                if current_size < last_pos:
                    last_pos = 0
                if current_size > last_pos:
                    try:
                        with open(p, "r", errors="replace") as f:
                            f.seek(last_pos)
                            new_data = f.read()
                            positions[p] = f.tell()
                        for line in new_data.splitlines():
                            if line.strip():
                                yield f"data: {json.dumps(line)}\n\n"
                    except Exception:
                        pass

            await asyncio.sleep(0.5)

    return StreamingResponse(
        tail_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Vault document viewer ---

@app.get("/read", response_class=HTMLResponse)
async def docs_index(request: Request):
    """List all readable markdown docs in vault/materials/."""
    materials = WORKSPACE.parent / "vault" / "materials"
    docs = []
    if materials.exists():
        for f in sorted(materials.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            docs.append({"name": f.stem, "filename": f.name, "size": f.stat().st_size,
                         "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    return HTMLResponse(_docs_list_html(docs))


@app.get("/read/{filename}", response_class=HTMLResponse)
async def docs_view(filename: str):
    """Render a vault markdown file as clean, mobile-friendly HTML."""
    # Security: only allow .md files from vault/materials
    if not filename.endswith(".md") or "/" in filename or ".." in filename:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    path = WORKSPACE.parent / "vault" / "materials" / filename
    if not path.exists():
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    raw = path.read_text()
    html = _md_to_html(raw, filename)
    return HTMLResponse(html)


def _md_to_html(md_text: str, title: str = "") -> str:
    """Convert markdown to clean, mobile-friendly HTML with styling."""
    import re
    # Strip YAML frontmatter
    if md_text.startswith("---"):
        end = md_text.find("---", 3)
        if end != -1:
            md_text = md_text[end + 3:].strip()
    # Simple markdown → HTML conversion
    lines = md_text.split("\n")
    html_lines = []
    in_code = False
    in_list = False
    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:]
                html_lines.append(f'<pre><code class="lang-{lang}">')
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue
        # Headers
        if line.startswith("######"):
            html_lines.append(f"<h6>{line[6:].strip()}</h6>")
        elif line.startswith("#####"):
            html_lines.append(f"<h5>{line[5:].strip()}</h5>")
        elif line.startswith("####"):
            html_lines.append(f"<h4>{line[4:].strip()}</h4>")
        elif line.startswith("###"):
            html_lines.append(f"<h3>{line[3:].strip()}</h3>")
        elif line.startswith("##"):
            html_lines.append(f"<h2>{line[2:].strip()}</h2>")
        elif line.startswith("#"):
            html_lines.append(f"<h1>{line[1:].strip()}</h1>")
        # Horizontal rule
        elif line.strip() in ("---", "***", "___"):
            html_lines.append("<hr>")
        # List items
        elif re.match(r"^\s*[-*]\s", line):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = re.sub(r"^\s*[-*]\s", "", line)
            html_lines.append(f"<li>{_inline_md(content)}</li>")
        # Numbered list
        elif re.match(r"^\s*\d+\.\s", line):
            if not in_list:
                html_lines.append("<ol>")
                in_list = True
            content = re.sub(r"^\s*\d+\.\s", "", line)
            html_lines.append(f"<li>{_inline_md(content)}</li>")
        else:
            if in_list:
                html_lines.append("</ul>" if html_lines[-2].startswith("<ul") or any("<ul>" in l for l in html_lines[-5:]) else "</ol>")
                in_list = False
            if line.strip():
                html_lines.append(f"<p>{_inline_md(line)}</p>")
            else:
                html_lines.append("")
    if in_list:
        html_lines.append("</ul>")
    body = "\n".join(html_lines)
    display_title = title.replace(".md", "").replace("-", " ").title()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{display_title}</title>
<style>
:root {{ --bg: #0d1117; --fg: #c9d1d9; --accent: #58a6ff; --muted: #8b949e;
         --border: #30363d; --code-bg: #161b22; --card: #161b22; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: var(--bg); color: var(--fg); line-height: 1.7;
        max-width: 800px; margin: 0 auto; padding: 20px 16px 60px; }}
h1 {{ font-size: 1.8em; margin: 1.2em 0 0.6em; color: #fff; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }}
h2 {{ font-size: 1.4em; margin: 1.4em 0 0.5em; color: var(--accent); }}
h3 {{ font-size: 1.15em; margin: 1.2em 0 0.4em; color: #d2a8ff; }}
h4,h5,h6 {{ font-size: 1em; margin: 1em 0 0.3em; color: var(--muted); }}
p {{ margin: 0.6em 0; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
ul, ol {{ margin: 0.5em 0 0.5em 1.5em; }}
li {{ margin: 0.25em 0; }}
pre {{ background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px;
       padding: 14px; overflow-x: auto; margin: 1em 0; font-size: 0.85em; }}
code {{ font-family: 'SF Mono', Menlo, monospace; }}
p code, li code {{ background: var(--code-bg); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
hr {{ border: none; border-top: 1px solid var(--border); margin: 2em 0; }}
strong {{ color: #fff; }}
blockquote {{ border-left: 3px solid var(--accent); padding-left: 1em; color: var(--muted); margin: 1em 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid var(--border); padding: 8px 12px; text-align: left; }}
th {{ background: var(--code-bg); color: var(--accent); }}
.back {{ display: inline-block; margin-bottom: 1em; color: var(--muted); font-size: 0.9em; }}
.back:hover {{ color: var(--accent); }}
</style>
</head>
<body>
<a href="/read" class="back">&larr; All documents</a>
{body}
</body>
</html>"""


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, code, links)."""
    import re
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__([^_]+)__', r'<strong>\1</strong>', text)
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    text = re.sub(r'_([^_]+)_', r'<em>\1</em>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


def _docs_list_html(docs: list[dict]) -> str:
    """Render doc index as HTML."""
    items = ""
    for d in docs:
        size_kb = round(d["size"] / 1024, 1)
        items += f"""<a href="/read/{d['filename']}" class="doc-card">
            <div class="doc-title">{d['name'].replace('-', ' ').title()}</div>
            <div class="doc-meta">{d['modified']} &middot; {size_kb} KB</div>
        </a>\n"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AOS Documents</title>
<style>
:root {{ --bg: #0d1117; --fg: #c9d1d9; --accent: #58a6ff; --muted: #8b949e;
         --border: #30363d; --card: #161b22; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: var(--bg); color: var(--fg); max-width: 800px;
        margin: 0 auto; padding: 20px 16px 60px; }}
h1 {{ font-size: 1.6em; margin-bottom: 1em; color: #fff; }}
.doc-card {{ display: block; background: var(--card); border: 1px solid var(--border);
             border-radius: 8px; padding: 16px; margin-bottom: 10px;
             text-decoration: none; color: var(--fg); transition: border-color 0.2s; }}
.doc-card:hover {{ border-color: var(--accent); }}
.doc-title {{ font-weight: 600; color: var(--accent); margin-bottom: 4px; }}
.doc-meta {{ font-size: 0.85em; color: var(--muted); }}
</style>
</head>
<body>
<h1>Documents</h1>
{items if items else '<p style="color: var(--muted);">No documents yet.</p>'}
</body>
</html>"""


# --- System Status (live mission control) ---


def _check_port(port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is listening."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _get_eventd_health() -> dict:
    """Fetch health info from eventd."""
    try:
        r = httpx.get("http://127.0.0.1:4097/health", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _get_eventd_events() -> list:
    """Fetch recent events from eventd."""
    try:
        r = httpx.get("http://127.0.0.1:4097/events", timeout=3)
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else r.json().get("events", [])
    except Exception:
        pass
    return []


def _get_people_stats() -> dict:
    """Query people.db for basic counts."""
    db_path = Path.home() / "vault" / "people" / "people.db"
    if not db_path.exists():
        return {"total": 0, "identifiers": 0, "groups": 0, "interactions_today": 0}
    try:
        conn = sqlite3.connect(str(db_path), timeout=3)
        total = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        identifiers = conn.execute("SELECT COUNT(*) FROM person_identifiers").fetchone()[0]
        groups = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        # Interactions today
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            interactions_today = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE date(timestamp) = ?",
                (today_str,)
            ).fetchone()[0]
        except Exception:
            interactions_today = 0
        conn.close()
        return {
            "total": total,
            "identifiers": identifiers,
            "groups": groups,
            "interactions_today": interactions_today,
        }
    except Exception:
        return {"total": 0, "identifiers": 0, "groups": 0, "interactions_today": 0}


def _get_triage_state() -> dict:
    """Read triage state for unanswered messages."""
    triage_file = Path.home() / ".aos" / "work" / "triage-state.json"
    try:
        if triage_file.exists():
            return json.loads(triage_file.read_text())
    except Exception:
        pass
    return {}


def _get_trust_info() -> dict:
    """Read comms trust level from trust.yaml."""
    trust_file = Path.home() / ".aos" / "config" / "trust.yaml"
    level_names = {0: "Observe", 1: "Surface", 2: "Assist", 3: "Autonomous"}
    try:
        if trust_file.exists():
            trust = yaml.safe_load(trust_file.read_text()) or {}
            # Check for comms capability across agents
            for agent_name, agent_data in trust.get("agents", {}).items():
                caps = agent_data.get("capabilities", {})
                if "communications" in caps:
                    level = caps["communications"]
                    return {"comms_level": level, "level_name": level_names.get(level, f"Level {level}")}
            # Fallback to global default
            level = trust.get("defaults", {}).get("comms_level", 0)
            return {"comms_level": level, "level_name": level_names.get(level, f"Level {level}")}
    except Exception:
        pass
    return {"comms_level": 0, "level_name": "Observe"}


def _get_work_summary() -> dict:
    """Get work system summary stats."""
    try:
        mod = _work_mod("engine")
        s = mod.summary()
        by_status = s.get("by_status", {})
        return {
            "active_tasks": by_status.get("active", 0),
            "total_tasks": s.get("total_tasks", 0),
            "todo_tasks": by_status.get("todo", 0),
            "done_tasks": by_status.get("done", 0),
            "projects": s.get("projects", 0),
            "goals": s.get("goals", 0),
            "threads": s.get("threads", 0),
            "inbox": s.get("inbox", 0),
        }
    except Exception:
        return {
            "active_tasks": 0, "total_tasks": 0, "todo_tasks": 0,
            "done_tasks": 0, "projects": 0, "goals": 0, "threads": 0, "inbox": 0,
        }


def _build_system_status() -> dict:
    """Build aggregated system status response."""
    # 1. Service health via port checks
    service_ports = {
        "dashboard": 4096,
        "eventd": 4097,
        "whatsapp": 7601,
        "bridge": 8880,
    }
    services = {}
    for name, port in service_ports.items():
        running = _check_port(port)
        services[name] = {
            "status": "running" if running else "down",
            "port": port,
        }

    # Enrich eventd with health data (nested under "bus" and "watchers" keys)
    eventd_health = _get_eventd_health()
    if eventd_health:
        bus = eventd_health.get("bus", {})
        services["eventd"]["events_total"] = bus.get("events_total", 0)
        services["eventd"]["consumers"] = bus.get("consumer_names", [])
        watchers = eventd_health.get("watchers", {})
    else:
        watchers = {}

    # 2. Communications
    triage = _get_triage_state()
    unanswered_raw = triage.get("unanswered", {})
    now = datetime.now()
    unanswered = []
    for key, entry in unanswered_raw.items():
        time_ago = "recently"
        try:
            ts = entry.get("received_at", "")
            if ts:
                msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if msg_dt.tzinfo:
                    now_aware = datetime.now(msg_dt.tzinfo)
                    delta = int((now_aware - msg_dt).total_seconds())
                else:
                    delta = int((now - msg_dt).total_seconds())
                if delta < 60:
                    time_ago = "just now"
                elif delta < 3600:
                    time_ago = f"{delta // 60}m ago"
                elif delta < 86400:
                    time_ago = f"{delta // 3600}h ago"
                else:
                    time_ago = f"{delta // 86400}d ago"
        except Exception:
            pass
        unanswered.append({
            "person_name": entry.get("person_name", key),
            "channel": entry.get("channel", "unknown"),
            "time_ago": time_ago,
        })

    # Recent events from eventd (events have nested "data" field)
    recent_events_raw = _get_eventd_events()
    recent_events = []
    for ev in recent_events_raw[:20]:
        ev_data = ev.get("data", {})
        recent_events.append({
            "type": ev.get("type", ""),
            "source": ev.get("source", ev_data.get("channel", "")),
            "timestamp": ev.get("timestamp", ""),
            "sender": ev_data.get("sender", ev_data.get("person_name", "")),
            "summary": (ev_data.get("text", "") or ev_data.get("summary", ""))[:120],
        })

    comms = {
        "watchers": watchers,
        "unanswered": unanswered,
        "recent_events": recent_events,
    }

    # 3. People DB stats
    people = _get_people_stats()

    # 4. Work summary
    work = _get_work_summary()

    # 5. Trust level
    trust = _get_trust_info()

    return {
        "services": services,
        "comms": comms,
        "people": people,
        "work": work,
        "trust": trust,
    }


@app.get("/api/system/status")
async def api_system_status():
    """Aggregated system status for the live mission control page."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _build_system_status)


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    """Live system status page — mission control."""
    active_count = 0
    try:
        tasks = _load_tasks()
        active_count = sum(1 for t in tasks if t.get("status") in ("active", "todo"))
    except Exception:
        pass
    return templates.TemplateResponse(request, "status.html", {
        "active_page": "status",
        "task_count": active_count if active_count else None,
    })


# ── Channels ──────────────────────────────────────────────

def _load_channels() -> dict:
    """Load all integrations from registry, enrich with live status from eventd."""
    # 1. Read registry
    registry_path = WORKSPACE / "core" / "integrations" / "registry.yaml"
    if not registry_path.exists():
        return {"channels": [], "categories": {}}

    try:
        reg = yaml.safe_load(registry_path.read_text()) or {}
    except Exception:
        return {"channels": [], "categories": {}}

    # 2. Fetch live watcher data from eventd
    eventd_health = _get_eventd_health()
    watchers = eventd_health.get("watchers", {}) if eventd_health else {}

    # 3. Check which secrets exist (batch via agent-secret check)
    all_requires: dict[str, list[str]] = {}  # integration_id -> required secrets
    tier_map = {
        "apple_native": 1,
        "builtin": 2,
        "catalog": 3,
    }

    channels = []
    for tier_key in ("apple_native", "builtin", "catalog"):
        tier_data = reg.get(tier_key, {})
        if not isinstance(tier_data, dict):
            continue
        for int_id, info in tier_data.items():
            if not isinstance(info, dict):
                continue
            channels.append({
                "id": int_id,
                "name": info.get("name", int_id.replace("_", " ").title()),
                "tier": info.get("tier", tier_map.get(tier_key, 3)),
                "category": info.get("category", "other"),
                "description": info.get("description", ""),
                "provides": info.get("provides", []),
                "requires": info.get("requires", []),
                "registry_status": info.get("status", "available"),
                "_tier_key": tier_key,
            })
            all_requires[int_id] = info.get("requires", [])

    # 4. Check credentials existence via agent-secret check (non-blocking, best effort)
    secret_exists: dict[str, bool] = {}
    secrets_to_check = set()
    for reqs in all_requires.values():
        for r in reqs:
            # Only check things that look like credential names (not permissions/apps)
            if "permission" not in r.lower() and "app" not in r.lower() and "sync" not in r.lower():
                secrets_to_check.add(r)

    if secrets_to_check:
        try:
            agent_secret = str(WORKSPACE / "core" / "bin" / "agent-secret")
            result = subprocess.run(
                [agent_secret, "check"] + list(secrets_to_check),
                capture_output=True, text=True, timeout=5,
            )
            # agent-secret check prints "name: yes/no" lines or exits 0 if all exist
            for line in result.stdout.strip().split("\n"):
                if ":" in line:
                    name, val = line.split(":", 1)
                    secret_exists[name.strip()] = val.strip().lower() in ("yes", "true", "found", "1")
                elif result.returncode == 0 and len(secrets_to_check) == 1:
                    secret_exists[list(secrets_to_check)[0]] = True
        except Exception:
            pass

    # 5. Determine status for each channel
    for ch in channels:
        int_id = ch["id"]
        # If registry says active, trust it
        if ch["registry_status"] == "active":
            ch["status"] = "active"
        # Check if there's a running watcher for this channel
        elif int_id in watchers and watchers[int_id].get("running", False):
            ch["status"] = "active"
        # Check if any related watcher name matches
        elif any(int_id in wk for wk in watchers if watchers[wk].get("running", False)):
            ch["status"] = "active"
        # Check if credentials exist
        elif any(secret_exists.get(r, False) for r in all_requires.get(int_id, [])
                 if "permission" not in r.lower()):
            ch["status"] = "connected"
        else:
            ch["status"] = "available"

        # Live data for communication channels
        live = {}
        watcher_data = watchers.get(int_id, {})
        if watcher_data:
            live["running"] = watcher_data.get("running", False)
            if watcher_data.get("last_event"):
                live["last_message"] = watcher_data["last_event"]
            if watcher_data.get("message_count"):
                live["message_count"] = watcher_data["message_count"]
        ch["live"] = live

        # Clean up internal keys
        del ch["registry_status"]
        del ch["_tier_key"]

    # 6. Build category summary
    categories = {}
    for ch in channels:
        cat = ch["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "active": 0}
        categories[cat]["count"] += 1
        if ch["status"] in ("active", "connected"):
            categories[cat]["active"] += 1

    return {"channels": channels, "categories": categories}


@app.get("/api/channels")
async def api_channels():
    """All channels from registry with live status."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _load_channels)


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request):
    """Channels page — all integrations grouped by category."""
    active_count = 0
    try:
        tasks = _load_tasks()
        active_count = sum(1 for t in tasks if t.get("status") in ("active", "todo"))
    except Exception:
        pass
    return templates.TemplateResponse(request, "channels.html", {
        "active_page": "channels",
        "task_count": active_count if active_count else None,
    })


@app.get("/trust", response_class=HTMLResponse)
async def trust_page(request: Request):
    """Trust map dashboard page."""
    active_count = 0
    try:
        tasks = _load_tasks()
        active_count = sum(1 for t in tasks if t.get("status") in ("active", "todo"))
    except Exception:
        pass
    return templates.TemplateResponse(request, "trust.html", {
        "active_page": "trust",
        "task_count": active_count if active_count else None,
    })


# ── Trust Observability APIs ─────────────────────────────────────────

_PEOPLE_DB = Path.home() / "vault" / "people" / "people.db"
_TRUST_YAML = Path.home() / ".aos" / "config" / "trust.yaml"
_GRADUATION_LOG = Path.home() / ".aos" / "logs" / "comms-graduation.log"
_PROPOSALS_FILE = Path.home() / ".aos" / "work" / "comms" / "graduation_proposals.json"
_AUTONOMOUS_LOG = Path.home() / ".aos" / "work" / "comms" / "autonomous_log.jsonl"


def _trust_db():
    """Get a read-only People DB connection for trust queries."""
    if not _PEOPLE_DB.exists():
        return None
    conn = sqlite3.connect(str(_PEOPLE_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _load_trust():
    try:
        return yaml.safe_load(_TRUST_YAML.read_text()) or {}
    except Exception:
        return {}


@app.get("/api/comms/trust-map")
async def api_trust_map():
    """Trust map — all people with their comms trust levels + stats."""
    conn = _trust_db()
    if not conn:
        return {"people": [], "summary": {}}

    trust_config = _load_trust()
    per_person = trust_config.get("comms", {}).get("per_person", {})

    rows = conn.execute("""
        SELECT p.id, p.canonical_name, p.importance,
               rs.msg_count_30d, rs.trajectory, rs.days_since_contact,
               rs.interaction_count_90d, rs.outbound_30d, rs.inbound_30d
        FROM people p
        LEFT JOIN relationship_state rs ON rs.person_id = p.id
        WHERE p.is_archived = 0 AND p.importance <= 3
        ORDER BY p.importance, rs.msg_count_30d DESC
    """).fetchall()

    people = []
    level_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    for r in rows:
        pid = r["id"]
        entry = per_person.get(pid, {})
        level = entry.get("level", 0) if isinstance(entry, dict) else 0
        level_counts[level] = level_counts.get(level, 0) + 1

        # Get acceptance rate from surface_feedback
        fb = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN operator_action IN ('accepted','edited') THEN 1 ELSE 0 END) as positive "
            "FROM surface_feedback WHERE person_id = ?",
            (pid,),
        ).fetchone()
        total_fb = fb["total"] if fb else 0
        acceptance_rate = (fb["positive"] / total_fb) if total_fb > 0 else None

        people.append({
            "person_id": pid,
            "name": r["canonical_name"],
            "importance": r["importance"],
            "level": level,
            "msg_count_30d": r["msg_count_30d"] or 0,
            "trajectory": r["trajectory"],
            "days_since_contact": r["days_since_contact"],
            "interaction_count_90d": r["interaction_count_90d"] or 0,
            "acceptance_rate": acceptance_rate,
        })

    conn.close()
    return {"people": people, "summary": level_counts}


@app.get("/api/comms/graduation-history")
async def api_graduation_history():
    """Graduation events from audit log, reverse chronological."""
    if not _GRADUATION_LOG.exists():
        return []
    events = []
    try:
        for line in _GRADUATION_LOG.read_text().strip().split("\n"):
            if line:
                events.append(json.loads(line))
    except Exception:
        return []
    events.reverse()
    return events[:50]


@app.get("/api/comms/graduation-proposals")
async def api_graduation_proposals():
    """Pending graduation proposals."""
    if not _PROPOSALS_FILE.exists():
        return []
    try:
        proposals = json.loads(_PROPOSALS_FILE.read_text())
        # Resolve names
        conn = _trust_db()
        if conn:
            for p in proposals:
                row = conn.execute(
                    "SELECT canonical_name FROM people WHERE id = ?",
                    (p.get("person_id", ""),),
                ).fetchone()
                if row:
                    p["name"] = row["canonical_name"]
            conn.close()
        return proposals
    except Exception:
        return []


@app.get("/api/comms/autonomous-log")
async def api_autonomous_log():
    """Recent autonomous actions."""
    if not _AUTONOMOUS_LOG.exists():
        return []
    actions = []
    try:
        for line in _AUTONOMOUS_LOG.read_text().strip().split("\n"):
            if line:
                actions.append(json.loads(line))
    except Exception:
        return []
    actions.reverse()
    return actions[:50]


@app.get("/api/comms/thresholds")
async def api_thresholds():
    """Current graduation thresholds."""
    config = _load_trust()
    return config.get("comms", {}).get("thresholds", {})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4096)
