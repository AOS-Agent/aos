"""Dashboard server — FastAPI on port 4096."""

import asyncio
import json
import shutil
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
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _load_yaml(path: str) -> dict:
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
        page_size = 16384
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
        jobs = r.json()
        active = sum(1 for j in jobs if j.get("status") == "running")
        services["listen"] = {"status": "online", "detail": f"{len(jobs)} jobs ({active} active)"}
    except Exception:
        services["listen"] = {"status": "offline", "detail": "Not responding"}

    # Bridge
    try:
        result = subprocess.run(["pgrep", "-f", "apps/bridge/main.py"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            services["bridge"] = {"status": "online", "detail": "Telegram + Slack"}
        else:
            services["bridge"] = {"status": "offline", "detail": "Not running"}
    except Exception:
        services["bridge"] = {"status": "offline", "detail": "Check failed"}

    # Memory MCP
    try:
        result = subprocess.run(["pgrep", "-f", "apps/memory/main.py"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            services["memory"] = {"status": "online", "detail": "MCP server"}
        else:
            services["memory"] = {"status": "offline", "detail": "Starts on demand"}
    except Exception:
        services["memory"] = {"status": "offline", "detail": "Check failed"}

    return services


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
        import importlib.util
        spec = importlib.util.spec_from_file_location("engine", str(Path.home() / "aos" / "core" / "work" / "engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tasks = mod.get_all_tasks()
        # Only return active tasks (not done/cancelled)
        return [t for t in tasks if t.get("status") not in ("done", "cancelled")]
    except Exception:
        return []


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    state = _load_yaml("config/state.yaml")
    health = _system_health()
    services = _service_status()
    agents = _load_agents()
    tasks = _load_tasks()
    activity = get_recent(30)
    agent_stats = get_agent_stats()
    conv_stats = get_conversation_stats()
    session_stats = get_session_stats()
    today_summary = get_today_summary()
    recent_sessions = get_recent_sessions_enriched(limit=10)

    # Active sessions for "Now" section
    active_sessions = [s for s in recent_sessions if s["status"] == "running"]
    # Last completed session for "last active X ago"
    last_completed = next((s for s in recent_sessions if s["status"] == "completed"), None)

    # Compute "last active ago" string
    last_active_ago = ""
    if last_completed and last_completed.get("ended_at"):
        try:
            ended = datetime.fromisoformat(last_completed["ended_at"])
            delta = (datetime.now(ended.tzinfo or __import__('datetime').timezone.utc) - ended).total_seconds()
            if delta < 60:
                last_active_ago = "just now"
            elif delta < 3600:
                last_active_ago = f"{int(delta // 60)}m ago"
            elif delta < 86400:
                last_active_ago = f"{int(delta // 3600)}h ago"
            else:
                last_active_ago = f"{int(delta // 86400)}d ago"
        except Exception:
            pass

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

    # Automations
    crons = _get_cron_jobs()
    launch_agents = _get_launch_agents()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "state": state,
        "health": health,
        "services": services,
        "agents": agents,
        "tasks": tasks,
        "clickup_tasks": [],
        "activity": activity,
        "agent_stats": {s["agent"]: s for s in agent_stats},
        "conv_stats": conv_stats,
        "session_stats": session_stats,
        "today": today_summary,
        "recent_sessions": recent_sessions,
        "active_sessions": active_sessions,
        "last_active_ago": last_active_ago,
        "health_status": health_status,
        "health_text": health_text,
        "now": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "crons": crons,
        "launch_agents": launch_agents,
    })


# --- API endpoints for HTMX partial updates ---

@app.get("/api/health")
async def api_health():
    return _system_health()


@app.get("/api/services")
async def api_services():
    return _service_status()


@app.get("/api/activity")
async def api_activity(limit: int = 30):
    return get_recent(limit)


@app.get("/api/work")
async def api_work():
    """Return v2 work system data."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("engine", str(Path.home() / "aos" / "core" / "work" / "engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = mod.load_all()
        summary = mod.summary()
        return {"tasks": data["tasks"], "projects": data["projects"], "goals": data["goals"], "threads": data["threads"], "inbox": data["inbox"], "summary": summary}
    except Exception as e:
        return {"error": str(e)}


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    agents = _load_agents()
    agent_stats = get_agent_stats()
    global_agents = [a for a in agents if a.get("scope") == "global"]
    project_groups = {}
    for a in agents:
        if a.get("scope") == "project" and a.get("project"):
            project_groups.setdefault(a["project"], []).append(a)
    return templates.TemplateResponse("agents.html", {
        "request": request,
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
    """SSE endpoint — pushes new activity + health updates to the dashboard."""
    async def event_generator():
        last_id = 0
        # Get current max ID
        recent = get_recent(1)
        if recent:
            last_id = recent[0]["id"]

        tick = 0
        while True:
            if await request.is_disconnected():
                break

            # Check for new activity every 2 seconds
            recent = get_recent(10)
            new_items = [r for r in recent if r["id"] > last_id]
            if new_items:
                last_id = max(r["id"] for r in new_items)
                # Send newest first → reverse to chronological for client
                for item in reversed(new_items):
                    yield f"event: activity\ndata: {json.dumps(item)}\n\n"

            # Send health + services every 15 seconds
            if tick % 15 == 0:
                health = _system_health()
                yield f"event: health\ndata: {json.dumps(health)}\n\n"
                services = _service_status()
                yield f"event: services\ndata: {json.dumps(services)}\n\n"

            tick += 2
            await asyncio.sleep(2)

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


@app.get("/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request):
    convs = get_conversations(limit=100)
    stats = get_conversation_stats()
    agents = _load_agents()
    # Get telegram config for agents
    projects_config = _load_yaml("config/projects.yaml")

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "active_page": "conversations",
        "conversations": convs,
        "stats": stats,
        "agents": agents,
        "projects_config": projects_config,
    })


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


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    sessions = get_sessions(limit=100)
    stats = get_session_stats()
    return templates.TemplateResponse("sessions.html", {
        "request": request,
        "active_page": "sessions",
        "sessions": sessions,
        "stats": stats,
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
        return path.replace("/Users/agentalhadi/", "~/") if path else ""

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

def _get_cron_jobs() -> list[dict]:
    """Parse crontab and return structured cron job info."""
    jobs = []
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            schedule = " ".join(parts[:5])
            command = parts[5]
            # Extract log redirect path
            log_path = None
            if ">>" in command:
                log_path = command.split(">>")[1].strip().split()[0]
            # Extract script name
            cmd_parts = command.split(">>")[0].strip().split()
            script = cmd_parts[-1] if cmd_parts else command
            name = Path(script).stem
            # Human-readable schedule
            human = _cron_to_human(schedule)
            # Last run from log file mtime
            last_run = None
            last_output = ""
            if log_path:
                lp = Path(log_path)
                if lp.exists():
                    last_run = datetime.fromtimestamp(
                        lp.stat().st_mtime, tz=ZoneInfo("America/Toronto")
                    ).isoformat()
                    # Last few lines of output
                    try:
                        lines = lp.read_text().strip().split("\n")
                        last_output = lines[-1][:120] if lines else ""
                    except Exception:
                        pass
            jobs.append({
                "name": name,
                "type": "cron",
                "schedule": schedule,
                "schedule_human": human,
                "command": command.split(">>")[0].strip(),
                "log_path": log_path,
                "last_run": last_run,
                "last_output": last_output,
                "status": "active",
            })
    except Exception:
        pass
    return jobs


def _cron_to_human(schedule: str) -> str:
    """Convert a cron schedule to a human-readable string."""
    mapping = {
        "*/30 * * * *": "Every 30 min",
        "0 */2 * * *": "Every 2 hours",
        "0 * * * *": "Every hour",
        "*/5 * * * *": "Every 5 min",
        "*/15 * * * *": "Every 15 min",
    }
    if schedule in mapping:
        return mapping[schedule]
    parts = schedule.split()
    if len(parts) == 5:
        m, h, dom, mon, dow = parts
        days = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
        if dow != "*" and h != "*":
            day = days.get(dow, dow)
            return f"{day} at {h}:{m.zfill(2)}"
        if h != "*" and m != "*":
            return f"Daily at {h}:{m.zfill(2)}"
    return schedule


def _get_launch_agents() -> list[dict]:
    """Get status of com.agent.* LaunchAgents."""
    agents = []
    la_dir = Path.home() / "Library" / "LaunchAgents"
    for plist in sorted(la_dir.glob("com.agent.*.plist")):
        label = plist.stem
        short_name = label.replace("com.agent.", "")
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
        "crons": _get_cron_jobs(),
        "launch_agents": _get_launch_agents(),
    }


@app.post("/api/automations/cron/{name}/run")
async def api_run_cron(name: str):
    """Trigger a cron job immediately via the Listen job server."""
    crons = _get_cron_jobs()
    job = next((c for c in crons if c["name"] == name), None)
    if not job:
        return {"error": "not found"}
    try:
        r = httpx.post(
            "http://localhost:7600/jobs",
            json={"command": job["command"], "name": f"manual:{name}"},
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
    return templates.TemplateResponse("crons.html", {
        "request": request,
        "active_page": "crons",
    })


@app.post("/api/automations/launchagent/{name}/restart")
async def api_restart_launchagent(name: str):
    """Restart a LaunchAgent via launchctl kickstart."""
    label = f"com.agent.{name}"
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/{label}"],
            capture_output=True, text=True, timeout=10,
        )
        return {"ok": result.returncode == 0, "output": result.stdout + result.stderr}
    except Exception as e:
        return {"error": str(e)}


# --- Logs page ---

LOG_DIR = WORKSPACE / "logs"

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


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    available = {}
    for name, paths in LOG_SOURCES.items():
        available[name] = any(p.exists() for p in paths)
    return templates.TemplateResponse("logs.html", {
        "request": request,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4096)
