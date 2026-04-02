#!/usr/bin/env python3
"""
STEER Job Tracker — manages automation job state in YAML files.

Jobs are stored at ~/.aos/steer/jobs/<job_id>.yaml
Each job tracks: prompt, status, updates, summary, apps opened, duration.

Used by:
- Bridge: creates jobs from Telegram messages
- Agent sessions: update progress, write summary
- Bridge: reads completed jobs, sends results back

Usage:
    python3 job.py create "Open Obsidian and find today's notes"
    python3 job.py update <job_id> "Opened Obsidian, found 5 daily notes"
    python3 job.py summary <job_id> "Found and organized 5 daily notes from this week"
    python3 job.py app <job_id> Obsidian          # track app opened
    python3 job.py done <job_id>
    python3 job.py fail <job_id> "Obsidian window not found"
    python3 job.py get <job_id>
    python3 job.py list
    python3 job.py active                          # show running jobs
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

JOBS_DIR = Path.home() / ".aos" / "steer" / "jobs"


def ensure_dir():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.yaml"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_job(job_id: str) -> dict:
    """Load job YAML. Uses simple key: value parsing, no pyyaml dependency."""
    p = job_path(job_id)
    if not p.exists():
        print(f"Error: job '{job_id}' not found", file=sys.stderr)
        sys.exit(1)

    data = {}
    current_list_key = None
    with open(p) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("  - ") and current_list_key:
                data[current_list_key].append(line[4:])
                continue
            current_list_key = None
            if ": " in line:
                key, val = line.split(": ", 1)
                key = key.strip()
                if val == "[]":
                    data[key] = []
                    current_list_key = key
                elif val.startswith('"') and val.endswith('"'):
                    data[key] = val[1:-1]
                elif val in ("true", "false"):
                    data[key] = val == "true"
                else:
                    data[key] = val
            elif line.endswith(":"):
                key = line[:-1].strip()
                data[key] = []
                current_list_key = key
    return data


def save_job(job_id: str, data: dict):
    """Save job as simple YAML."""
    p = job_path(job_id)
    lines = []
    for key, val in data.items():
        if isinstance(val, list):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in val:
                    lines.append(f"  - {item}")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        else:
            lines.append(f'{key}: "{val}"')
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")


def cmd_create(prompt: str):
    ensure_dir()
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    data = {
        "id": job_id,
        "prompt": prompt,
        "status": "pending",
        "created_at": now_iso(),
        "started_at": "",
        "completed_at": "",
        "apps_opened": [],
        "updates": [],
        "summary": "",
        "error": "",
    }
    save_job(job_id, data)
    print(json.dumps({"job_id": job_id, "status": "pending"}))
    return job_id


def cmd_start(job_id: str):
    data = load_job(job_id)
    data["status"] = "running"
    data["started_at"] = now_iso()
    save_job(job_id, data)
    print(json.dumps({"job_id": job_id, "status": "running"}))


def cmd_update(job_id: str, message: str):
    data = load_job(job_id)
    data["updates"].append(f"[{now_iso()}] {message}")
    save_job(job_id, data)
    print(json.dumps({"job_id": job_id, "update": message}))


def cmd_app(job_id: str, app_name: str):
    data = load_job(job_id)
    if app_name not in data["apps_opened"]:
        data["apps_opened"].append(app_name)
    save_job(job_id, data)


def cmd_summary(job_id: str, text: str):
    data = load_job(job_id)
    data["summary"] = text
    save_job(job_id, data)


def cmd_done(job_id: str):
    data = load_job(job_id)
    data["status"] = "completed"
    data["completed_at"] = now_iso()
    save_job(job_id, data)
    # Output for bridge to read
    print(json.dumps({
        "job_id": job_id,
        "status": "completed",
        "summary": data.get("summary", ""),
        "apps_opened": data.get("apps_opened", []),
        "updates": data.get("updates", []),
    }))


def cmd_fail(job_id: str, error: str):
    data = load_job(job_id)
    data["status"] = "failed"
    data["error"] = error
    data["completed_at"] = now_iso()
    save_job(job_id, data)
    print(json.dumps({"job_id": job_id, "status": "failed", "error": error}))


def cmd_get(job_id: str):
    data = load_job(job_id)
    print(json.dumps(data, indent=2))


def cmd_list():
    ensure_dir()
    jobs = []
    for f in sorted(JOBS_DIR.glob("*.yaml"), reverse=True):
        data = load_job(f.stem)
        jobs.append({
            "id": data.get("id", f.stem),
            "status": data.get("status", "?"),
            "prompt": data.get("prompt", "")[:60],
            "created_at": data.get("created_at", ""),
        })
    print(json.dumps(jobs, indent=2))


def cmd_active():
    ensure_dir()
    active = []
    for f in JOBS_DIR.glob("*.yaml"):
        data = load_job(f.stem)
        if data.get("status") in ("pending", "running"):
            active.append({
                "id": data.get("id", f.stem),
                "status": data.get("status"),
                "prompt": data.get("prompt", "")[:60],
                "updates": data.get("updates", [])[-3:],
            })
    print(json.dumps(active, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create" and len(sys.argv) >= 3:
        cmd_create(" ".join(sys.argv[2:]))
    elif cmd == "start" and len(sys.argv) >= 3:
        cmd_start(sys.argv[2])
    elif cmd == "update" and len(sys.argv) >= 4:
        cmd_update(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "summary" and len(sys.argv) >= 4:
        cmd_summary(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "app" and len(sys.argv) >= 4:
        cmd_app(sys.argv[2], sys.argv[3])
    elif cmd == "done" and len(sys.argv) >= 3:
        cmd_done(sys.argv[2])
    elif cmd == "fail" and len(sys.argv) >= 4:
        cmd_fail(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "get" and len(sys.argv) >= 3:
        cmd_get(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "active":
        cmd_active()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
