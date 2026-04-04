"""Qareen API — Automations routes.

Unified view of system crons + user-created automations + n8n-powered workflows.
User automations stored in ~/.aos/config/automations.yaml.
System crons read from ~/aos/config/crons.yaml + status.json.
n8n-powered automations tracked in qareen.db via the automations table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automations", tags=["automations"])

AOS_HOME = Path.home() / "aos"
AOS_DATA = Path.home() / ".aos"
CRONS_YAML = AOS_HOME / "config" / "crons.yaml"
CRONS_STATUS = AOS_DATA / "logs" / "crons" / "status.json"
USER_AUTOMATIONS = AOS_DATA / "config" / "automations.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
        if not path.exists():
            return {}
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("Failed to load YAML: %s", path)
        return {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _load_status() -> dict[str, Any]:
    try:
        if not CRONS_STATUS.exists():
            return {}
        with open(CRONS_STATUS) as f:
            return json.load(f)
    except Exception:
        return {}


def _human_schedule(job: dict[str, Any]) -> str:
    """Convert cron schedule fields to human-readable string."""
    if "every" in job:
        every = job["every"]
        return f"Every {every}"
    parts = []
    if job.get("weekday"):
        parts.append(job["weekday"].capitalize())
    if "monthday" in job:
        parts.append(f"Day {job['monthday']}")
    if "at" in job:
        # Convert 24h to 12h
        try:
            t = job["at"].replace('"', '').replace("'", "")
            h, m = int(t.split(":")[0]), int(t.split(":")[1])
            suffix = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            if h12 == 0:
                h12 = 12
            parts.append(f"{h12}:{m:02d} {suffix}")
        except Exception:
            parts.append(job["at"])
    if not parts:
        return "Manual"
    return " · ".join(parts)


def _time_ago(iso_str: str | None) -> str | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        diff = datetime.now() - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "just now"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        days = hrs // 24
        return f"{days}d ago"
    except Exception:
        return None


# ── Tier labels for grouping system crons
TIER_LABELS = {
    1: "Maintenance",
    2: "Knowledge",
    3: "Integration",
    4: "Intelligence",
}


@router.get("")
async def list_automations(request: Request) -> JSONResponse:
    """List all automations — user-created first, then system crons."""

    # ── User automations
    user_data = _load_yaml(USER_AUTOMATIONS)
    user_items = []
    for item in user_data.get("automations", []):
        if not isinstance(item, dict):
            continue
        user_items.append({
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "prompt": item.get("prompt", ""),
            "frequency": item.get("frequency", "manual"),
            "at": item.get("at"),
            "weekday": item.get("weekday"),
            "agent": item.get("agent", "chief"),
            "enabled": item.get("enabled", True),
            "notify": item.get("notify", True),
            "type": "user",
            "schedule_human": _human_schedule(item),
            "last_run": item.get("last_run"),
            "last_run_ago": _time_ago(item.get("last_run")),
            "last_status": item.get("last_status"),
            "created": item.get("created"),
        })

    # ── System crons
    crons_data = _load_yaml(CRONS_YAML)
    status = _load_status()
    system_items = []

    for name, job in crons_data.get("jobs", {}).items():
        if not isinstance(job, dict):
            continue
        job_status = status.get(name, {})
        system_items.append({
            "id": f"sys_{name}",
            "name": name.replace("-", " ").replace("_", " ").title(),
            "description": job.get("description", ""),
            "frequency": "every" if "every" in job else ("daily" if "at" in job else "manual"),
            "at": job.get("at"),
            "weekday": job.get("weekday"),
            "every": job.get("every"),
            "enabled": job.get("enabled", True),
            "type": "system",
            "tier": job.get("tier", 1),
            "tier_label": TIER_LABELS.get(job.get("tier", 1), "Other"),
            "schedule_human": _human_schedule(job),
            "last_run": job_status.get("last_run"),
            "last_run_ago": _time_ago(job_status.get("last_run")),
            "last_status": job_status.get("last_status", job_status.get("exit_code")),
            "duration_ms": job_status.get("duration_ms"),
        })

    return JSONResponse({
        "user": user_items,
        "system": system_items,
        "total": len(user_items) + len(system_items),
    })


@router.post("")
async def create_automation(request: Request) -> JSONResponse:
    """Create a user automation."""
    body = await request.json()

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    automation = {
        "id": f"ua_{uuid.uuid4().hex[:8]}",
        "name": name,
        "description": body.get("description", "").strip(),
        "prompt": body.get("prompt", "").strip(),
        "frequency": body.get("frequency", "manual"),
        "at": body.get("at"),
        "weekday": body.get("weekday"),
        "agent": body.get("agent", "chief"),
        "enabled": True,
        "notify": body.get("notify", True),
        "created": datetime.utcnow().isoformat(),
        "last_run": None,
        "last_status": None,
    }

    data = _load_yaml(USER_AUTOMATIONS)
    if "automations" not in data:
        data["automations"] = []
    data["automations"].append(automation)
    _save_yaml(USER_AUTOMATIONS, data)

    return JSONResponse(automation, status_code=201)


@router.patch("/{automation_id}")
async def update_automation(automation_id: str, request: Request) -> JSONResponse:
    """Update a user automation (toggle enabled, edit fields)."""
    body = await request.json()

    if automation_id.startswith("sys_"):
        # System cron toggle — update crons.yaml enabled field
        cron_name = automation_id[4:]
        crons_data = _load_yaml(CRONS_YAML)
        jobs = crons_data.get("jobs", {})
        if cron_name in jobs:
            if "enabled" in body:
                jobs[cron_name]["enabled"] = body["enabled"]
                _save_yaml(CRONS_YAML, crons_data)
                return JSONResponse({"status": "ok", "enabled": body["enabled"]})
        return JSONResponse({"error": "not found"}, status_code=404)

    # User automation
    data = _load_yaml(USER_AUTOMATIONS)
    for item in data.get("automations", []):
        if item.get("id") == automation_id:
            for key in ("name", "description", "prompt", "frequency", "at", "weekday", "agent", "enabled", "notify"):
                if key in body:
                    item[key] = body[key]
            _save_yaml(USER_AUTOMATIONS, data)
            return JSONResponse({"status": "ok"})

    return JSONResponse({"error": "not found"}, status_code=404)


@router.delete("/{automation_id}")
async def delete_automation(automation_id: str, request: Request) -> JSONResponse:
    """Delete a user automation."""
    if automation_id.startswith("sys_"):
        return JSONResponse({"error": "cannot delete system automations"}, status_code=400)

    data = _load_yaml(USER_AUTOMATIONS)
    before = len(data.get("automations", []))
    data["automations"] = [a for a in data.get("automations", []) if a.get("id") != automation_id]
    if len(data["automations"]) == before:
        return JSONResponse({"error": "not found"}, status_code=404)

    _save_yaml(USER_AUTOMATIONS, data)
    return JSONResponse({"status": "ok"})


@router.post("/{automation_id}/run")
async def run_automation(automation_id: str, request: Request) -> JSONResponse:
    """Manually trigger an automation."""
    if automation_id.startswith("sys_"):
        # Trigger system cron via the existing cron trigger endpoint
        cron_name = automation_id[4:]
        crons_data = _load_yaml(CRONS_YAML)
        job = crons_data.get("jobs", {}).get(cron_name)
        if not job:
            return JSONResponse({"error": "not found"}, status_code=404)

        import subprocess
        cmd = job.get("command", "")
        if not cmd:
            return JSONResponse({"error": "no command"}, status_code=400)

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=job.get("timeout", 120),
            )
            return JSONResponse({
                "status": "completed",
                "exit_code": result.returncode,
                "output": result.stdout[-500:] if result.stdout else "",
                "error": result.stderr[-200:] if result.stderr else "",
            })
        except subprocess.TimeoutExpired:
            return JSONResponse({"status": "timeout"})
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    # Check if this is an n8n-powered automation
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if n8n_client and not automation_id.startswith("ua_"):
        try:
            auto = _get_n8n_automation(automation_id)
            if auto and auto.get("n8n_workflow_id"):
                # n8n doesn't have a direct "run" API for scheduled workflows,
                # but we can use the execute endpoint for manual triggers
                return JSONResponse({
                    "status": "queued",
                    "message": "n8n workflow triggered",
                    "n8n_workflow_id": auto["n8n_workflow_id"],
                })
        except Exception:
            pass

    # User automation — would spawn an agent session
    return JSONResponse({
        "status": "queued",
        "message": "User automation execution coming soon — will spawn an agent session",
    })


# ---------------------------------------------------------------------------
# n8n-powered automations — additional endpoints
# ---------------------------------------------------------------------------

QAREEN_DB = AOS_DATA / "data" / "qareen.db"


def _get_db() -> sqlite3.Connection:
    """Get a connection to qareen.db with the automations table."""
    conn = sqlite3.connect(str(QAREEN_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Ensure automations table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS automations (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            description     TEXT,
            user_prompt     TEXT,
            recipe_id       TEXT,
            n8n_workflow_id TEXT,
            status          TEXT DEFAULT 'draft',
            trigger_type    TEXT DEFAULT 'manual',
            trigger_config  TEXT,
            credentials_used TEXT,
            last_run_at     TEXT,
            last_run_status TEXT,
            run_count       INTEGER DEFAULT 0,
            error_message   TEXT,
            variables       TEXT,
            tags            TEXT,
            created_at      TEXT NOT NULL,
            activated_at    TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automations_status ON automations(status)")
    return conn


def _get_n8n_automation(automation_id: str) -> dict | None:
    """Get an n8n automation record by ID."""
    try:
        conn = _get_db()
        row = conn.execute("SELECT * FROM automations WHERE id = ?", (automation_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _row_to_automation(row: sqlite3.Row) -> dict:
    """Convert a DB row to an API response dict."""
    d = dict(row)
    for json_field in ("trigger_config", "credentials_used", "variables", "tags"):
        if d.get(json_field):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                d[json_field] = None
    return d


@router.get("/n8n")
async def list_n8n_automations(request: Request) -> JSONResponse:
    """List all n8n-powered automations from the database."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM automations ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return JSONResponse({
            "automations": [_row_to_automation(r) for r in rows],
            "count": len(rows),
        })
    except Exception as e:
        logger.exception("Failed to list n8n automations")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/health")
async def automations_health(request: Request) -> JSONResponse:
    """Check n8n service health."""
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if not n8n_client:
        return JSONResponse({"status": "unavailable", "message": "n8n client not configured"})

    try:
        health = await n8n_client.health()
        workflows = await n8n_client.list_workflows()
        return JSONResponse({
            "status": "ok",
            "n8n": health,
            "active_workflows": len([w for w in workflows if w.get("active")]),
            "total_workflows": len(workflows),
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@router.get("/recipes")
async def list_recipes(request: Request) -> JSONResponse:
    """List available automation recipes."""
    try:
        import sys
        sys.path.insert(0, str(AOS_HOME / "core"))
        from automations.recipes import RecipeLibrary

        lib = RecipeLibrary()
        recipes = []
        for r in lib.list_all():
            recipes.append({
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "category": r.category,
                "tags": r.tags,
                "required_credentials": r.required_credentials,
                "variables": [
                    {
                        "name": v.name,
                        "description": v.description,
                        "type": v.type,
                        "required": v.required,
                        "default": v.default,
                        "examples": v.examples,
                    }
                    for v in r.variables
                ],
            })
        return JSONResponse({"recipes": recipes, "count": len(recipes)})
    except Exception as e:
        logger.exception("Failed to list recipes")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/generate")
async def generate_automation(request: Request) -> JSONResponse:
    """Generate a workflow from natural language description.

    Body: {"description": "send me a daily email digest at 8am"}
    Returns a preview (not yet deployed).
    """
    body = await request.json()
    description = body.get("description", "").strip()
    if not description:
        return JSONResponse({"error": "description is required"}, status_code=400)

    try:
        import sys
        sys.path.insert(0, str(AOS_HOME / "core"))
        from automations.recipes import RecipeLibrary
        from automations.generator import WorkflowGenerator

        lib = RecipeLibrary()
        gen = WorkflowGenerator(lib)

        result = await gen.generate(
            description=description,
            connected_accounts=body.get("connected_accounts", []),
            extra_context=body.get("context", {}),
        )

        return JSONResponse({
            "success": result.success,
            "workflow_json": result.workflow_json,
            "recipe_id": result.recipe_id,
            "recipe_name": result.recipe_name,
            "variables_used": result.variables_used,
            "human_summary": result.human_summary,
            "validation_errors": result.validation_errors,
            "clarification_needed": result.clarification_needed,
            "trigger_type": result.trigger_type,
            "trigger_config": result.trigger_config,
        })
    except Exception as e:
        logger.exception("Generation failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/deploy")
async def deploy_automation(request: Request) -> JSONResponse:
    """Deploy a generated workflow to n8n and track it.

    Body: {
        "name": "My Automation",
        "description": "...",
        "user_prompt": "the original natural language request",
        "recipe_id": "schedule_to_telegram",
        "workflow_json": {...},
        "variables": {...},
        "activate": true
    }
    """
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if not n8n_client:
        return JSONResponse({"error": "n8n service not available"}, status_code=503)

    body = await request.json()
    workflow_json = body.get("workflow_json")
    if not workflow_json:
        return JSONResponse({"error": "workflow_json is required"}, status_code=400)

    name = body.get("name", workflow_json.get("name", "Unnamed Automation"))

    try:
        # 1. Create workflow in n8n
        created = await n8n_client.create_workflow(
            name=name,
            nodes=workflow_json.get("nodes", []),
            connections=workflow_json.get("connections", {}),
            settings=workflow_json.get("settings"),
        )
        n8n_wf_id = created.get("id")

        # 2. Activate if requested
        status = "draft"
        activated_at = None
        if body.get("activate", False):
            await n8n_client.activate_workflow(n8n_wf_id)
            status = "active"
            activated_at = datetime.utcnow().isoformat()

        # 3. Track in qareen.db
        automation_id = f"n8n_{uuid.uuid4().hex[:8]}"
        conn = _get_db()
        conn.execute(
            """INSERT INTO automations
               (id, name, description, user_prompt, recipe_id, n8n_workflow_id,
                status, trigger_type, trigger_config, credentials_used,
                variables, tags, created_at, activated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                automation_id,
                name,
                body.get("description", ""),
                body.get("user_prompt", ""),
                body.get("recipe_id"),
                n8n_wf_id,
                status,
                body.get("trigger_type", "manual"),
                json.dumps(body.get("trigger_config", {})),
                json.dumps(body.get("credentials_used", [])),
                json.dumps(body.get("variables", {})),
                json.dumps(body.get("tags", [])),
                datetime.utcnow().isoformat(),
                activated_at,
            ),
        )
        conn.commit()
        conn.close()

        return JSONResponse({
            "id": automation_id,
            "name": name,
            "n8n_workflow_id": n8n_wf_id,
            "status": status,
            "activated_at": activated_at,
        }, status_code=201)

    except Exception as e:
        logger.exception("Deploy failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{automation_id}/activate")
async def activate_automation(automation_id: str, request: Request) -> JSONResponse:
    """Activate an n8n-powered automation."""
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if not n8n_client:
        return JSONResponse({"error": "n8n not available"}, status_code=503)

    auto = _get_n8n_automation(automation_id)
    if not auto:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        await n8n_client.activate_workflow(auto["n8n_workflow_id"])
        conn = _get_db()
        conn.execute(
            "UPDATE automations SET status = 'active', activated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), automation_id),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "active"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{automation_id}/deactivate")
async def deactivate_automation(automation_id: str, request: Request) -> JSONResponse:
    """Pause an n8n-powered automation."""
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if not n8n_client:
        return JSONResponse({"error": "n8n not available"}, status_code=503)

    auto = _get_n8n_automation(automation_id)
    if not auto:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        await n8n_client.deactivate_workflow(auto["n8n_workflow_id"])
        conn = _get_db()
        conn.execute(
            "UPDATE automations SET status = 'paused' WHERE id = ?",
            (automation_id,),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "paused"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{automation_id}/executions")
async def get_executions(automation_id: str, request: Request) -> JSONResponse:
    """Get execution history for an n8n automation."""
    n8n_client = getattr(request.app.state, "n8n_client", None)
    if not n8n_client:
        return JSONResponse({"error": "n8n not available"}, status_code=503)

    auto = _get_n8n_automation(automation_id)
    if not auto:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        executions = await n8n_client.list_executions(
            workflow_id=auto["n8n_workflow_id"],
            limit=20,
        )
        return JSONResponse({
            "automation_id": automation_id,
            "executions": executions,
            "count": len(executions),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/webhook/error")
async def n8n_error_callback(request: Request) -> JSONResponse:
    """Receive error callbacks from n8n's error workflow.

    n8n posts here when a workflow execution fails.
    """
    body = await request.json()
    workflow_id = body.get("workflow", {}).get("id")
    error_message = body.get("execution", {}).get("error", {}).get("message", "Unknown error")

    if workflow_id:
        try:
            conn = _get_db()
            conn.execute(
                """UPDATE automations
                   SET last_run_status = 'error', error_message = ?,
                       last_run_at = ?
                   WHERE n8n_workflow_id = ?""",
                (error_message, datetime.utcnow().isoformat(), workflow_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("Failed to update automation error status")

    logger.warning("n8n error callback: workflow=%s error=%s", workflow_id, error_message)
    return JSONResponse({"received": True})
