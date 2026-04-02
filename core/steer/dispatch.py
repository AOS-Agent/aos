#!/usr/bin/env python3
"""
STEER Dispatcher — spawns Claude Code sessions in tmux for GUI automation tasks.

Flow:
  1. Create job YAML via job.py
  2. Create tmux session via Drive
  3. Run Claude with autonomous-execution skill + job tracking instructions
  4. Return job ID for polling

Usage:
    from core.steer.dispatch import dispatch_steer_job, poll_job, is_job_done

    job_id = dispatch_steer_job("Open Obsidian and find today's notes")
    # ... later ...
    result = poll_job(job_id)
    if result["status"] == "completed":
        print(result["summary"])
"""
import json
import subprocess
import sys
import time
from pathlib import Path

# Paths
DRIVE_DIR = Path.home() / "aos" / "vendor" / "mac-mini-agent-tools" / "apps" / "drive"
DRIVE = DRIVE_DIR / "main.py"
DRIVE_PYTHON = DRIVE_DIR / ".venv" / "bin" / "python3"  # Drive has its own venv with psutil
JOB_SCRIPT = Path(__file__).parent / "job.py"
JOBS_DIR = Path.home() / ".aos" / "steer" / "jobs"

# The system prompt appended to every STEER job session
STEER_SYSTEM_PROMPT = """
You are running as a STEER automation job. Your job ID is: {job_id}
Your job file is at: {job_path}

## Your Tools

STEER (GUI automation):
  STEER=~/aos/vendor/mac-mini-agent-tools/apps/steer/.build/arm64-apple-macosx/release/steer
  $STEER see --app "App" --json       # observe
  $STEER click --on B1 --app "App"    # act (invisible-first)
  $STEER type "text" --into T1        # type (invisible-first)
  $STEER hotkey cmd+o --app "App"     # hotkey (targeted to app PID)
  $STEER wait --for "element" --app "App" --timeout 5  # wait for UI
  $STEER cleanup --opened "apps" --clear-old  # cleanup

## Rules

1. OBSERVE before every action: run `steer see` first
2. ONE action at a time: never chain steer commands
3. VERIFY after every action: run `steer see` again
4. WAIT, don't sleep: use `steer wait` instead of `sleep`
5. CLEANUP at the end: close apps you opened, clear temp files

## Progress Tracking

After each meaningful step, update the job:
  python3 {job_script} update {job_id} "description of what you did"
  python3 {job_script} app {job_id} AppName   # track apps you open

When finished:
  python3 {job_script} summary {job_id} "concise summary of what was accomplished"
  python3 {job_script} done {job_id}
  $STEER cleanup --opened "App1,App2" --clear-old

If you fail:
  python3 {job_script} fail {job_id} "what went wrong"
"""


def dispatch_steer_job(prompt: str, *, agent: str = "chief", cwd: str = None) -> str:
    """
    Dispatch a STEER automation job.

    1. Creates a job YAML
    2. Spawns a detached tmux session
    3. Runs Claude inside it with STEER instructions
    4. Returns the job ID for polling

    Args:
        prompt: The task description from the operator
        agent: Which agent to use (default: chief)
        cwd: Working directory for the session

    Returns:
        job_id: The job identifier for polling
    """
    # 1. Create job
    result = subprocess.run(
        [sys.executable, str(JOB_SCRIPT), "create", prompt],
        capture_output=True, text=True
    )
    job_data = json.loads(result.stdout)
    job_id = job_data["job_id"]
    job_path = JOBS_DIR / f"{job_id}.yaml"

    # 2. Start the job
    subprocess.run(
        [sys.executable, str(JOB_SCRIPT), "start", job_id],
        capture_output=True, text=True
    )

    # 3. Build the system prompt
    sys_prompt = STEER_SYSTEM_PROMPT.format(
        job_id=job_id,
        job_path=job_path,
        job_script=JOB_SCRIPT,
    )

    # Write system prompt to temp file (too long for CLI arg)
    prompt_file = Path(f"/tmp/steer-job-{job_id}-prompt.txt")
    sys_prompt_file = Path(f"/tmp/steer-job-{job_id}-system.txt")
    prompt_file.write_text(prompt)
    sys_prompt_file.write_text(sys_prompt)

    # 4. Build Claude command
    claude_cmd = (
        f'claude -p "$(cat {prompt_file})"'
        f' --append-system-prompt "$(cat {sys_prompt_file})"'
        f' --dangerously-skip-permissions'
        f' --model sonnet'
    )
    if agent:
        claude_cmd += f' --agent {agent}'

    # 5. Spawn tmux session
    session_name = f"steer-{job_id[:15]}"
    work_dir = cwd or str(Path.home())

    # Use Drive to create a detached session
    create_result = subprocess.run(
        [str(DRIVE_PYTHON), str(DRIVE), "session", "create", session_name,
         "--detach", "--dir", work_dir, "--json"],
        capture_output=True, text=True
    )
    if create_result.returncode != 0:
        # Mark job as failed
        subprocess.run([sys.executable, str(JOB_SCRIPT), "fail", job_id,
                       f"Failed to create tmux session: {create_result.stderr}"],
                      capture_output=True, text=True)
        raise RuntimeError(f"tmux session creation failed: {create_result.stderr}")

    # 6. Send Claude command to the session
    sentinel_cmd = f'{claude_cmd} ; echo "__STEER_JOB_DONE_{job_id}:$?"'
    subprocess.run(
        [str(DRIVE_PYTHON), str(DRIVE), "send", session_name, sentinel_cmd, "--enter"],
        capture_output=True, text=True
    )

    return job_id


def poll_job(job_id: str) -> dict:
    """
    Check the status of a STEER job.

    Returns:
        dict with: id, status, summary, updates, apps_opened, error
    """
    result = subprocess.run(
        [sys.executable, str(JOB_SCRIPT), "get", job_id],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"id": job_id, "status": "unknown", "error": result.stderr}
    return json.loads(result.stdout)


def is_job_done(job_id: str) -> bool:
    """Check if a job has finished (completed or failed)."""
    data = poll_job(job_id)
    return data.get("status") in ("completed", "failed")


def wait_for_job(job_id: str, *, timeout: float = 300, poll_interval: float = 3.0) -> dict:
    """
    Block until a job completes or timeout.

    Args:
        timeout: Max seconds to wait (default 5 min)
        poll_interval: How often to check (default 3s)

    Returns:
        Final job data
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = poll_job(job_id)
        if data.get("status") in ("completed", "failed"):
            return data
        time.sleep(poll_interval)
    return {"id": job_id, "status": "timeout", "error": f"Job did not complete within {timeout}s"}


def cleanup_job(job_id: str):
    """Clean up tmux session and temp files after a job."""
    session_name = f"steer-{job_id[:15]}"

    # Kill tmux session if still running
    subprocess.run(
        [str(DRIVE_PYTHON), str(DRIVE), "session", "kill", session_name],
        capture_output=True, text=True
    )

    # Clean temp files
    for f in Path("/tmp").glob(f"steer-job-{job_id}*"):
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    """CLI interface for testing."""
    if len(sys.argv) < 3:
        print("Usage: python3 dispatch.py run 'prompt here'")
        print("       python3 dispatch.py poll <job_id>")
        print("       python3 dispatch.py wait <job_id>")
        print("       python3 dispatch.py cleanup <job_id>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "run":
        prompt = " ".join(sys.argv[2:])
        job_id = dispatch_steer_job(prompt)
        print(json.dumps({"job_id": job_id, "status": "dispatched"}))
    elif cmd == "poll":
        print(json.dumps(poll_job(sys.argv[2]), indent=2))
    elif cmd == "wait":
        result = wait_for_job(sys.argv[2])
        print(json.dumps(result, indent=2))
    elif cmd == "cleanup":
        cleanup_job(sys.argv[2])
        print("Cleaned up")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
