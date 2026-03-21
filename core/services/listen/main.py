import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI()

# Mount Chief endpoints (app lives at ~/chief-ios-app, server endpoints stay here)
try:
    sys.path.insert(0, str(Path.home() / "chief-ios-app"))
    from server_endpoints import router as chief_router
    app.include_router(chief_router)
except Exception as e:
    print(f"Warning: Could not load Chief endpoints: {e}")

# Mount Chief chat stream endpoints
try:
    from chat_stream import router as chat_stream_router
    app.include_router(chat_stream_router)
except Exception as e:
    print(f"Warning: Could not load Chief chat stream endpoints: {e}")

# Mount Chief transcription endpoint
try:
    from transcription_endpoint import router as transcription_router
    app.include_router(transcription_router)
except Exception as e:
    print(f"Warning: Could not load Chief transcription endpoint: {e}")

JOBS_DIR = Path(__file__).parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
ARCHIVED_DIR = JOBS_DIR / "archived"


class JobRequest(BaseModel):
    prompt: str


@app.post("/job")
def create_job(req: JobRequest):
    job_id = uuid4().hex[:8]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    job_data = {
        "id": job_id,
        "status": "running",
        "prompt": req.prompt,
        "created_at": now,
        "pid": 0,
        "updates": [],
        "summary": "",
    }

    # Write YAML before spawning worker (worker reads it on startup)
    job_file = JOBS_DIR / f"{job_id}.yaml"
    with open(job_file, "w") as f:
        yaml.dump(job_data, f, default_flow_style=False, sort_keys=False)

    # Spawn the worker process
    worker_path = Path(__file__).parent / "worker.py"
    proc = subprocess.Popen(
        [sys.executable, str(worker_path), job_id, req.prompt],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Update PID after spawn
    job_data["pid"] = proc.pid
    with open(job_file, "w") as f:
        yaml.dump(job_data, f, default_flow_style=False, sort_keys=False)

    return {"job_id": job_id, "status": "running"}


@app.get("/job/{job_id}", response_class=PlainTextResponse)
def get_job(job_id: str):
    job_file = JOBS_DIR / f"{job_id}.yaml"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return job_file.read_text()


@app.get("/jobs", response_class=PlainTextResponse)
def list_jobs(archived: bool = False):
    search_dir = ARCHIVED_DIR if archived else JOBS_DIR
    jobs = []
    for f in sorted(search_dir.glob("*.yaml")):
        with open(f) as fh:
            data = yaml.safe_load(fh)
        jobs.append({
            "id": data.get("id"),
            "status": data.get("status"),
            "prompt": data.get("prompt"),
            "created_at": data.get("created_at"),
        })
    result = yaml.dump({"jobs": jobs}, default_flow_style=False, sort_keys=False)
    return result


@app.post("/jobs/clear")
def clear_jobs():
    ARCHIVED_DIR.mkdir(exist_ok=True)
    count = 0
    for f in JOBS_DIR.glob("*.yaml"):
        shutil.move(str(f), str(ARCHIVED_DIR / f.name))
        count += 1
    return {"archived": count}


@app.delete("/job/{job_id}")
def stop_job(job_id: str):
    job_file = JOBS_DIR / f"{job_id}.yaml"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    with open(job_file) as f:
        data = yaml.safe_load(f)

    pid = data.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    data["status"] = "stopped"
    with open(job_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return {"job_id": job_id, "status": "stopped"}


# ── Health Data Endpoint ──────────────────────────────────
# Receives Apple Health data from iPhone Shortcut via Tailscale.
# Stores raw JSON + writes summary to daily note.

HEALTH_DIR = Path.home() / ".aos" / "data" / "health"
VAULT_DAILY = Path.home() / "vault" / "daily"
VAULT_TEMPLATES = Path.home() / "vault" / "templates"

from pydantic import Field
from typing import Optional


class HealthSample(BaseModel):
    type: str                         # e.g., "sleep", "steps", "distance", "flights"
    value: Optional[float] = None     # numeric value (steps count, distance in km, etc.)
    start: Optional[str] = None       # ISO datetime
    end: Optional[str] = None         # ISO datetime
    unit: Optional[str] = None        # e.g., "count", "km", "hr"
    source: Optional[str] = None      # e.g., "iPhone", "Apple Watch"


class HealthPayload(BaseModel):
    date: str                                    # YYYY-MM-DD
    # V2 format: top-level counts from iPhone Shortcut
    steps: Optional[float] = None
    distance: Optional[float] = None
    flights: Optional[float] = None
    sleep: Optional[float] = None
    # V1 format: detailed samples array
    samples: list[HealthSample] = Field(default_factory=list)
    raw: Optional[dict] = None                   # raw data passthrough


from fastapi import Request


@app.post("/health/sync")
async def sync_health_data(request: Request):
    """Receive full health data from HealthSync iOS app. Accepts any JSON with a date field."""
    import json, re
    from datetime import datetime as _dt

    raw_body = await request.body()
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    try:
        body = json.loads(raw_body)
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    date = str(body.get("date", _dt.now().strftime("%Y-%m-%d")))

    # Store ALL health data as-is
    raw_file = HEALTH_DIR / f"{date}.json"
    with open(raw_file, "w") as f:
        json.dump(body, f, indent=2)

    # Extract key metrics for daily note
    steps = int(body.get("steps", 0))
    distance = body.get("distance", 0)
    flights = int(body.get("flights", 0))
    sleep_hours = body.get("sleep_analysis", 0)
    active_energy = int(body.get("active_energy", 0))
    exercise_time = int(body.get("exercise_time", 0))
    heart_rate = body.get("heart_rate", 0)
    resting_hr = body.get("resting_heart_rate", 0)

    # Sleep score
    if sleep_hours > 0:
        sleep_score = 5 if sleep_hours >= 8 else 4 if sleep_hours >= 7 else 3 if sleep_hours >= 6 else 2 if sleep_hours >= 5 else 1
    else:
        sleep_score = None

    # Update daily note
    daily_file = VAULT_DAILY / f"{date}.md"
    if not daily_file.exists():
        template = VAULT_TEMPLATES / "daily.md"
        if template.exists():
            content = template.read_text()
            content = content.replace("{{date}}", date)
            try:
                day_name = _dt.strptime(date, "%Y-%m-%d").strftime("%A")
            except Exception:
                day_name = ""
            content = content.replace("{{day}}", day_name)
            daily_file.parent.mkdir(parents=True, exist_ok=True)
            daily_file.write_text(content)

    if daily_file.exists():
        content = daily_file.read_text()
        if sleep_score and "sleep:" in content:
            content = re.sub(r'^sleep:.*$', f'sleep: {sleep_score}', content, count=1, flags=re.MULTILINE)

        health_summary = "\n## Health Data\n\n"
        if sleep_hours > 0:
            health_summary += f"- Sleep: {sleep_hours:.1f}h (score: {sleep_score}/5)\n"
        if steps > 0:
            health_summary += f"- Steps: {steps:,}\n"
        if distance > 0:
            health_summary += f"- Distance: {distance:.1f} km\n"
        if flights > 0:
            health_summary += f"- Flights: {flights}\n"
        if active_energy > 0:
            health_summary += f"- Active energy: {active_energy} kcal\n"
        if exercise_time > 0:
            health_summary += f"- Exercise: {exercise_time} min\n"
        if heart_rate > 0:
            health_summary += f"- Avg heart rate: {heart_rate:.0f} bpm\n"
        if resting_hr > 0:
            health_summary += f"- Resting heart rate: {resting_hr:.0f} bpm\n"

        if "## Health Data" not in content:
            content = content.rstrip() + "\n" + health_summary
        else:
            content = re.sub(
                r'## Health Data\n.*?(?=\n## |\Z)',
                health_summary.strip() + "\n",
                content,
                flags=re.DOTALL,
            )
        daily_file.write_text(content)

    return {
        "status": "ok",
        "date": date,
        "metrics_received": len([k for k, v in body.items() if k != "date" and v]),
    }


@app.post("/health")
async def receive_health_data(request: Request):
    """Receive health data from iPhone Shortcut (legacy)."""
    import json
    import re

    # Read raw body first for debugging
    raw_body = await request.body()
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    debug_log = HEALTH_DIR / "debug.log"
    with open(debug_log, "a") as dl:
        import datetime as _dt
        dl.write(f"\n--- {_dt.datetime.now().isoformat()} ---\n")
        dl.write(f"Content-Type: {request.headers.get('content-type', 'unknown')}\n")
        dl.write(f"Body ({len(raw_body)} bytes): {raw_body.decode('utf-8', errors='replace')[:2000]}\n")

    # Try to parse as JSON
    try:
        body = json.loads(raw_body)
    except Exception:
        return {"status": "error", "message": "Invalid JSON", "raw": raw_body.decode('utf-8', errors='replace')[:500]}

    # Build payload from whatever keys are present
    payload = HealthPayload(
        date=str(body.get("date", "")),
        steps=body.get("steps"),
        distance=body.get("distance"),
        flights=body.get("flights"),
        sleep=body.get("sleep"),
    )

    # Store raw payload
    raw_file = HEALTH_DIR / f"{payload.date}.json"
    with open(raw_file, "w") as f:
        json.dump(payload.model_dump(), f, indent=2)

    # Extract key metrics — support both V1 (samples array) and V2 (top-level counts)
    sleep_hours = 0.0
    steps = 0
    distance_km = 0.0
    flights = 0

    # V2 format (top-level fields from iPhone Shortcut)
    if payload.steps is not None:
        steps = int(payload.steps)
    if payload.distance is not None:
        distance_km = payload.distance
    if payload.flights is not None:
        flights = int(payload.flights)
    if payload.sleep is not None:
        sleep_hours = payload.sleep

    # V1 format (samples array — fallback)
    for s in payload.samples:
        t = s.type.lower()
        if "sleep" in t and s.value:
            sleep_hours += s.value
        elif "step" in t and s.value:
            steps += int(s.value)
        elif "distance" in t and s.value:
            distance_km += s.value
        elif "flight" in t and s.value:
            flights += int(s.value)

    # Convert sleep hours to 1-5 score
    if sleep_hours > 0:
        if sleep_hours >= 8:
            sleep_score = 5
        elif sleep_hours >= 7:
            sleep_score = 4
        elif sleep_hours >= 6:
            sleep_score = 3
        elif sleep_hours >= 5:
            sleep_score = 2
        else:
            sleep_score = 1
    else:
        sleep_score = None

    # Update today's daily note
    daily_file = VAULT_DAILY / f"{payload.date}.md"
    if not daily_file.exists():
        template = VAULT_TEMPLATES / "daily.md"
        if template.exists():
            content = template.read_text()
            content = content.replace("{{date}}", payload.date)
            # Guess day name from date
            from datetime import datetime as _dt
            try:
                day_name = _dt.strptime(payload.date, "%Y-%m-%d").strftime("%A")
            except Exception:
                day_name = ""
            content = content.replace("{{day}}", day_name)
            daily_file.parent.mkdir(parents=True, exist_ok=True)
            daily_file.write_text(content)

    if daily_file.exists():
        content = daily_file.read_text()
        # Update frontmatter fields
        if sleep_score and "sleep:" in content:
            content = re.sub(r'^sleep:.*$', f'sleep: {sleep_score}', content, count=1, flags=re.MULTILINE)
        # Add health data to Notes section
        health_summary = f"\n## Health Data\n\n"
        if sleep_hours > 0:
            health_summary += f"- Sleep: {sleep_hours:.1f}h (score: {sleep_score}/5)\n"
        if steps > 0:
            health_summary += f"- Steps: {steps:,}\n"
        if distance_km > 0:
            health_summary += f"- Distance: {distance_km:.1f} km\n"
        if flights > 0:
            health_summary += f"- Flights climbed: {flights}\n"

        if "## Health Data" not in content:
            content = content.rstrip() + "\n" + health_summary
        else:
            # Replace existing health data section
            content = re.sub(
                r'## Health Data\n.*?(?=\n## |\Z)',
                health_summary.strip() + "\n",
                content,
                flags=re.DOTALL,
            )
        daily_file.write_text(content)

    return {
        "status": "ok",
        "date": payload.date,
        "sleep_hours": sleep_hours,
        "sleep_score": sleep_score,
        "steps": steps,
        "distance_km": distance_km,
        "flights": flights,
    }


@app.get("/health/push")
async def push_health_data(
    steps: float = 0, distance: float = 0, flights: float = 0, sleep: float = 0,
    active_energy: float = 0, date: str = ""
):
    """Simple GET endpoint for iPhone Shortcuts — no JSON body needed.
    Usage: /health/push?steps=3200&distance=2.1&flights=1&sleep=7.5&active_energy=450
    """
    import json, re
    from datetime import datetime as _dt
    from fastapi import Request as _Req

    # Log the raw request for debugging
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    debug_log = HEALTH_DIR / "debug.log"
    with open(debug_log, "a") as dl:
        dl.write(f"\n--- {_dt.now().isoformat()} GET /health/push ---\n")
        dl.write(f"steps={steps} distance={distance} flights={flights} sleep={sleep} active_energy={active_energy} date={date}\n")

    if not date:
        date = _dt.now().strftime("%Y-%m-%d")

    payload = HealthPayload(date=date, steps=steps, distance=distance, flights=flights, sleep=sleep)

    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    raw_file = HEALTH_DIR / f"{payload.date}.json"
    save_data = payload.model_dump()
    save_data["active_energy"] = active_energy
    with open(raw_file, "w") as f:
        json.dump(save_data, f, indent=2)

    # Reuse the same daily-note logic
    sleep_hours = sleep
    if sleep_hours > 0:
        sleep_score = 5 if sleep_hours >= 8 else 4 if sleep_hours >= 7 else 3 if sleep_hours >= 6 else 2 if sleep_hours >= 5 else 1
    else:
        sleep_score = None

    daily_file = VAULT_DAILY / f"{date}.md"
    if not daily_file.exists():
        template = VAULT_TEMPLATES / "daily.md"
        if template.exists():
            content = template.read_text()
            content = content.replace("{{date}}", date)
            try:
                day_name = _dt.strptime(date, "%Y-%m-%d").strftime("%A")
            except Exception:
                day_name = ""
            content = content.replace("{{day}}", day_name)
            daily_file.parent.mkdir(parents=True, exist_ok=True)
            daily_file.write_text(content)

    if daily_file.exists():
        content = daily_file.read_text()
        if sleep_score and "sleep:" in content:
            content = re.sub(r'^sleep:.*$', f'sleep: {sleep_score}', content, count=1, flags=re.MULTILINE)
        health_summary = f"\n## Health Data\n\n"
        if sleep_hours > 0:
            health_summary += f"- Sleep: {sleep_hours:.1f}h (score: {sleep_score}/5)\n"
        if steps > 0:
            health_summary += f"- Steps: {int(steps):,}\n"
        if distance > 0:
            health_summary += f"- Distance: {distance:.1f} km\n"
        if flights > 0:
            health_summary += f"- Flights climbed: {int(flights)}\n"
        if active_energy > 0:
            health_summary += f"- Active energy: {int(active_energy)} kcal\n"
        if "## Health Data" not in content:
            content = content.rstrip() + "\n" + health_summary
        else:
            content = re.sub(
                r'## Health Data\n.*?(?=\n## |\Z)',
                health_summary.strip() + "\n",
                content,
                flags=re.DOTALL,
            )
        daily_file.write_text(content)

    return {
        "status": "ok",
        "date": date,
        "sleep_hours": sleep_hours,
        "sleep_score": sleep_score,
        "steps": int(steps),
        "distance_km": distance,
        "flights": int(flights),
        "active_energy": int(active_energy),
    }


@app.get("/health/{date}")
def get_health_data(date: str):
    """Retrieve stored health data for a date."""
    import json
    raw_file = HEALTH_DIR / f"{date}.json"
    if not raw_file.exists():
        raise HTTPException(status_code=404, detail=f"No health data for {date}")
    return json.loads(raw_file.read_text())


@app.get("/health")
def list_health_data():
    """List all dates with health data."""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    dates = sorted([f.stem for f in HEALTH_DIR.glob("*.json")], reverse=True)
    return {"dates": dates, "count": len(dates)}


if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 — Tailscale is the security boundary for remote access.
    # macOS firewall blocks non-Tailscale external traffic by default.
    uvicorn.run(app, host="0.0.0.0", port=7600)
