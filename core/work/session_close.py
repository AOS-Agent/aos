#!/usr/bin/env python3
"""
AOS Session Close Hook

Runs on SessionEnd. Three jobs:
1. Log session to ~/.aos/logs/sessions.jsonl
2. Link session to work system tasks and threads
3. Detect untracked work — scan transcript for tool calls (Write, Edit, Bash)
   and if substantial work happened but no tasks were started/completed/created,
   log a "session_untracked" activity event so it shows up in the dashboard.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info)
- Async hook — no stdout expected
"""

import json
import re
import sys
import os
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

try:
    import glob as _glob
except ImportError:
    _glob = None

LOG_DIR = Path.home() / ".aos" / "logs"
LOG_FILE = LOG_DIR / "sessions.jsonl"
DASHBOARD_URL = "http://127.0.0.1:4096"

# Add work engine to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _estimate_session_scope(transcript: str) -> dict:
    """Analyze transcript to estimate what happened.

    Returns: {
        tool_calls: int,
        files_modified: int,
        has_writes: bool,
        has_commits: bool,
        work_tracked: bool (did agent use work CLI during session),
        summary_hint: str,
    }
    """
    if not transcript:
        return {"tool_calls": 0, "files_modified": 0, "has_writes": False,
                "has_commits": False, "work_tracked": False, "summary_hint": ""}

    # Count tool usage signals
    write_count = len(re.findall(r'(?:Write|Edit)\s*\(', transcript))
    bash_count = len(re.findall(r'Bash\s*\(', transcript))
    tool_calls = write_count + bash_count

    # File modifications
    file_paths = set(re.findall(r'file_path["\s:=]+([^\s"\']+)', transcript))
    files_modified = len(file_paths)

    # Git commits
    has_commits = bool(re.search(r'git commit', transcript))

    # Did the agent use the work CLI?
    work_cli_calls = re.findall(r'work/cli\.py\s+(add|done|start|subtask|handoff)', transcript)
    work_tracked = len(work_cli_calls) > 0

    # Generate a hint about what happened
    hint_parts = []
    if files_modified > 0:
        hint_parts.append(f"{files_modified} files modified")
    if has_commits:
        hint_parts.append("commits made")
    if bash_count > 5:
        hint_parts.append(f"{bash_count} commands run")
    summary_hint = ", ".join(hint_parts) if hint_parts else ""

    return {
        "tool_calls": tool_calls,
        "files_modified": files_modified,
        "has_writes": write_count > 0,
        "has_commits": has_commits,
        "work_tracked": work_tracked,
        "summary_hint": summary_hint,
    }


def main():
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", os.getcwd())
    transcript = hook_input.get("transcript", "")

    # --- Step 1: Log to sessions.jsonl (always, even if work system fails) ---
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "cwd": cwd,
        "event": "session_end",
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Notify dashboard of session end (fire-and-forget)
    try:
        notify_data = json.dumps({
            "hook_type": "stop",
            "payload": {"session_id": session_id}
        }).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/sessions/hook",
            data=notify_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # Dashboard may not be running

    # --- Step 2: Link to work system ---
    try:
        import engine
    except ImportError:
        return  # Work engine not available, skip silently

    if session_id == "unknown":
        return  # No session ID, can't link

    # Check for a session-context file that the inject_context hook may have written
    context_file = Path.home() / ".aos" / "work" / ".session-context.json"
    explicit_task_ids = []
    session_ctx = {}
    if context_file.exists():
        try:
            session_ctx = json.loads(context_file.read_text())
            if session_ctx.get("session_id") == session_id:
                explicit_task_ids = session_ctx.get("task_ids", [])
            context_file.unlink(missing_ok=True)
        except Exception:
            pass

    # Link to explicitly referenced tasks
    for task_id in explicit_task_ids:
        engine.link_session_to_task(task_id, session_id)

    # Find active tasks for this project directory and link
    project_tasks = engine.find_tasks_by_project_or_cwd(cwd)
    active_tasks = [t for t in project_tasks if t.get("status") == "active"]
    for task in active_tasks:
        engine.link_session_to_task(task["id"], session_id)

    # Thread continuity — find or create thread for this cwd
    home = str(Path.home())
    known_project_dirs = [
        os.path.join(home, "aos"),
        os.path.join(home, "nuchay"),
        os.path.join(home, "chief-ios-app"),
    ]
    is_project_dir = cwd in known_project_dirs or any(
        cwd.startswith(d) for d in known_project_dirs
    )

    if is_project_dir:
        engine.get_or_create_thread_for_cwd(cwd, session_id)

    # --- Step 2b: Update initiative documents ---
    # initiative_ids were stored in session context by inject_context.py
    initiative_ids = session_ctx.get("initiative_ids", []) if session_ctx else []

    if _glob and initiative_ids:
        try:
            init_dir = os.path.join(str(Path.home()), "vault", "knowledge", "initiatives")
            today = datetime.now().strftime("%Y-%m-%d")
            for fpath in _glob.glob(os.path.join(init_dir, "*.md")):
                try:
                    with open(fpath) as f:
                        content = f.read()
                    fm_end = content.find("---", 3)
                    if fm_end == -1:
                        continue
                    fm_text = content[3:fm_end]
                    # Check if this initiative's title matches
                    title_match = False
                    for line in fm_text.split("\n"):
                        if line.startswith("title:"):
                            title_val = line.split(":", 1)[1].strip().strip('"').strip("'")
                            if title_val in initiative_ids:
                                title_match = True
                            break
                    if not title_match:
                        continue
                    # Surgical replacement: update only the 'updated:' line
                    if re.search(r'^updated:', fm_text, re.MULTILINE):
                        new_fm = re.sub(r'^updated:.*$', f'updated: {today}', fm_text, flags=re.MULTILINE)
                    else:
                        # No updated field — append it before the closing ---
                        new_fm = fm_text.rstrip("\n") + f"\nupdated: {today}\n"
                    new_content = "---" + new_fm + "---" + content[fm_end + 3:]
                    # Atomic write
                    tmp_path = fpath + ".tmp"
                    with open(tmp_path, "w") as f:
                        f.write(new_content)
                    os.replace(tmp_path, fpath)
                except Exception:
                    pass  # never crash session_close for initiative updates
        except Exception:
            pass  # never crash

    # --- Step 3: Detect untracked work ---
    scope = _estimate_session_scope(transcript)

    # Log session activity regardless
    project_id = engine.detect_project_from_cwd(cwd)
    dir_name = Path(cwd).name

    if scope["files_modified"] > 0 or scope["tool_calls"] > 3:
        # Substantial work happened
        if not scope["work_tracked"] and not active_tasks:
            # No work CLI usage AND no active tasks — work went untracked
            engine._log_activity(
                "session_untracked",
                detail=scope["summary_hint"] or f"Session in {dir_name} with {scope['tool_calls']} tool calls",
                project=project_id,
            )
        else:
            # Work was tracked — log a normal session event
            engine._log_activity(
                "session_end",
                detail=scope["summary_hint"] or f"Session in {dir_name}",
                project=project_id,
            )


if __name__ == "__main__":
    main()
