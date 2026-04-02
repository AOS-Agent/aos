"""Qareen Ontology Listeners — Event-driven side effects.

Replaces inline side effects from core/work/engine.py with decoupled
event handlers wired through the EventBus. Each handler is async,
fire-and-forget safe (never crashes), and logs exceptions.

Listeners:
  on_task_activity        — Appends to ~/.aos/work/activity.yaml
  on_task_dashboard_notify — POSTs event to dashboard SSE endpoint
  on_task_github_sync      — Creates/closes GitHub issues for aos project
  on_task_initiative_sync  — Checks off initiative doc checkboxes
  on_task_cascade_notify   — Notifies dashboard when parent auto-completes
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..events.bus import EventBus
from ..events.types import (
    Event,
    TaskCompleted,
    TaskCreated,
    TaskDeleted,
    TaskUpdated,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────

WORK_DIR = Path.home() / ".aos" / "work"
ACTIVITY_FILE = WORK_DIR / "activity.yaml"
MAX_ACTIVITY = 100

DASHBOARD_URL = "http://127.0.0.1:4096"
AOS_REPO = "hishamalhadi/aos"

# Map event_type to human-readable action names for the activity log
_ACTION_MAP = {
    "task.created": "task_created",
    "task.completed": "task_completed",
    "task.updated": "task_updated",
    "task.deleted": "task_deleted",
}


# ── Helpers ──────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _event_to_activity(event: Event) -> dict:
    """Build an activity log entry dict from a task event."""
    entry: dict = {
        "ts": _now(),
        "action": _ACTION_MAP.get(event.event_type, event.event_type),
    }
    if hasattr(event, "task_id") and event.task_id:
        entry["task_id"] = event.task_id
    if hasattr(event, "title") and event.title:
        entry["title"] = event.title
    if hasattr(event, "project") and event.project:
        entry["project"] = event.project
    return entry


def _event_to_notify_dict(event: Event) -> dict:
    """Build a notification dict from a task event."""
    data: dict = {
        "ts": _now(),
        "action": _ACTION_MAP.get(event.event_type, event.event_type),
    }
    if hasattr(event, "task_id") and event.task_id:
        data["task_id"] = event.task_id
    if hasattr(event, "title") and event.title:
        data["title"] = event.title
    if hasattr(event, "project") and event.project:
        data["project"] = event.project
    return data


# ── Blocking I/O functions (run via asyncio.to_thread) ───────

def _write_activity_sync(event: Event) -> None:
    """Append an event to the activity YAML log. Blocking, file-locked."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    entry = _event_to_activity(event)

    activity_lock = WORK_DIR / ".activity.lock"
    with open(activity_lock, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            events: list = []
            if ACTIVITY_FILE.exists():
                with open(ACTIVITY_FILE, "r") as f:
                    events = yaml.safe_load(f) or []
            events.insert(0, entry)
            events = events[:MAX_ACTIVITY]

            fd, tmp = tempfile.mkstemp(
                dir=str(WORK_DIR), suffix=".activity.tmp"
            )
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(
                        events, f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(ACTIVITY_FILE))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _gh_create_issue_sync(
    task_id: str, title: str, priority: int = 3
) -> str | None:
    """Create a GitHub Issue for an AOS task. Returns issue URL or None."""
    labels = f"task,P{priority}"
    body = f"**Task ID:** `{task_id}`"

    result = subprocess.run(
        [
            "gh", "issue", "create",
            "--repo", AOS_REPO,
            "--title", f"[{task_id}] {title}",
            "--body", body,
            "--label", labels,
        ],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        logger.info("Created GitHub issue: %s", url)
        return url
    logger.warning("gh issue create failed: %s", result.stderr[:200])
    return None


def _gh_close_issue_sync(task_id: str) -> bool:
    """Close a GitHub Issue by searching for its task ID in the title."""
    # Find the issue number
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", AOS_REPO,
            "--search", f"[{task_id}] in:title",
            "--state", "open",
            "--json", "number",
            "--limit", "1",
        ],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return False

    issues = json.loads(result.stdout)
    if not issues:
        return False

    issue_num = issues[0]["number"]
    close_result = subprocess.run(
        [
            "gh", "issue", "close",
            "--repo", AOS_REPO,
            str(issue_num),
        ],
        capture_output=True, text=True, timeout=15,
    )
    if close_result.returncode == 0:
        logger.info("Closed GitHub issue #%d for %s", issue_num, task_id)
        return True
    return False


def _sync_initiative_checkbox_sync(event: TaskCompleted) -> None:
    """Check off a matching checkbox in an initiative doc.

    Reads source_ref from event.payload. If the referenced markdown file
    exists, finds the checkbox matching the task ID or title and marks it
    done. Also updates the 'updated:' date in frontmatter.
    """
    source_ref = event.payload.get("source_ref")
    if not source_ref:
        return

    doc_path = Path.home() / source_ref.lstrip("~/")
    if not doc_path.exists():
        # Try as relative to home
        doc_path = Path.home() / source_ref
    if not doc_path.exists():
        return

    content = doc_path.read_text()
    title = event.title or ""
    task_id = event.task_id or ""

    # Match checkbox by task ID reference or title substring
    updated = False
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if not re.match(r'\s*- \[ \]', line):
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
        # Update the 'updated:' date in frontmatter
        today = datetime.now().strftime("%Y-%m-%d")
        new_content = "\n".join(lines)
        new_content = re.sub(
            r'^updated:.*$', f'updated: {today}',
            new_content, count=1, flags=re.MULTILINE,
        )
        # Atomic write
        tmp = str(doc_path) + ".tmp"
        Path(tmp).write_text(new_content)
        os.replace(tmp, str(doc_path))
        logger.info("Initiative checkbox synced: %s in %s", task_id, doc_path)


# ── Async Event Handlers ────────────────────────────────────


async def on_task_activity(event: Event) -> None:
    """Log task events to ~/.aos/work/activity.yaml.

    Handles: task.created, task.completed, task.updated, task.deleted.
    Appends event dict (ts, action, task_id, title, project) to YAML file.
    Keeps last 100 entries. Uses file locking (fcntl).
    """
    try:
        await asyncio.to_thread(_write_activity_sync, event)
    except Exception:
        logger.exception("on_task_activity failed for %s", event.event_type)


async def on_task_dashboard_notify(event: Event) -> None:
    """POST event data to dashboard for instant SSE push.

    Handles: task.* (all task events).
    Fire-and-forget, 1 second timeout, silent on failure.
    """
    try:
        data = json.dumps(_event_to_notify_dict(event)).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/work/notify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # urlopen is blocking — run in thread
        await asyncio.to_thread(urllib.request.urlopen, req, timeout=1)
    except Exception:
        pass  # Dashboard may not be running — silent


async def on_task_github_sync(event: Event) -> None:
    """Sync task events to GitHub Issues for the AOS project.

    On task.created: if project == "aos", create a GitHub issue.
    On task.completed: if project == "aos", find and close the matching issue.
    """
    try:
        project = getattr(event, "project", None)
        if project != "aos":
            return

        task_id = getattr(event, "task_id", "")
        if not task_id:
            return

        if event.event_type == "task.created":
            title = getattr(event, "title", "")
            priority = 3
            if hasattr(event, "priority"):
                # TaskPriority enum — get .value if it's an enum
                p = event.priority
                priority = p.value if hasattr(p, "value") else int(p)
            await asyncio.to_thread(
                _gh_create_issue_sync, task_id, title, priority
            )

        elif event.event_type == "task.completed":
            await asyncio.to_thread(_gh_close_issue_sync, task_id)

    except Exception:
        logger.exception("on_task_github_sync failed for %s", event.event_type)


async def on_task_initiative_sync(event: Event) -> None:
    """Sync task completion to initiative document checkboxes.

    On task.completed: if the task has a source_ref in payload pointing
    to an initiative doc in vault, find the matching checkbox and check
    it off. Also updates the 'updated:' date in frontmatter.
    """
    if not isinstance(event, TaskCompleted):
        return
    try:
        await asyncio.to_thread(_sync_initiative_checkbox_sync, event)

        # If checkbox was synced, notify the dashboard
        source_ref = event.payload.get("source_ref")
        if source_ref:
            notify_data = {
                "action": "initiative_update",
                "title": event.title or "",
                "detail": f"Checkbox synced: {event.task_id}",
                "task_id": event.task_id or "",
                "ts": datetime.now().isoformat(),
            }
            data = json.dumps(notify_data).encode()
            req = urllib.request.Request(
                f"{DASHBOARD_URL}/api/work/notify",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                await asyncio.to_thread(
                    urllib.request.urlopen, req, timeout=1
                )
            except Exception:
                pass  # Dashboard may not be running
    except Exception:
        logger.exception(
            "on_task_initiative_sync failed for %s", event.event_type
        )


async def on_task_cascade_notify(event: Event) -> None:
    """Notify dashboard when a parent task auto-completed via subtask cascade.

    On task.completed: if event.payload indicates a parent auto-completed
    (parent_auto_completed=True, parent_id, parent_title present), emit
    a phase_completed initiative event to the dashboard.
    """
    if not isinstance(event, TaskCompleted):
        return
    try:
        parent_id = event.payload.get("parent_auto_completed_id")
        parent_title = event.payload.get("parent_auto_completed_title")
        parent_project = event.payload.get("parent_auto_completed_project")

        if not parent_id:
            return

        # A parent task was auto-completed — notify dashboard
        notify_data = {
            "action": "phase_completed",
            "title": parent_title or parent_id,
            "task_id": parent_id,
            "ts": datetime.now().isoformat(),
        }
        if parent_project:
            notify_data["project"] = parent_project

        data = json.dumps(notify_data).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/work/notify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            await asyncio.to_thread(urllib.request.urlopen, req, timeout=1)
        except Exception:
            pass  # Dashboard may not be running

        logger.info(
            "Cascade notify: parent %s auto-completed via %s",
            parent_id, event.task_id,
        )
    except Exception:
        logger.exception(
            "on_task_cascade_notify failed for %s", event.event_type
        )


# ── Registration ─────────────────────────────────────────────


def register_listeners(bus: EventBus) -> None:
    """Wire all ontology listeners to the event bus.

    Call this once during system startup to connect side effects
    to task lifecycle events.
    """
    # Activity logging — all task events
    bus.subscribe("task.created", on_task_activity)
    bus.subscribe("task.completed", on_task_activity)
    bus.subscribe("task.updated", on_task_activity)
    bus.subscribe("task.deleted", on_task_activity)

    # Dashboard SSE push — all task events (wildcard)
    bus.subscribe("task.*", on_task_dashboard_notify)

    # GitHub issue sync — create and complete only
    bus.subscribe("task.created", on_task_github_sync)
    bus.subscribe("task.completed", on_task_github_sync)

    # Initiative document checkbox sync — complete only
    bus.subscribe("task.completed", on_task_initiative_sync)

    # Cascade parent auto-complete notification — complete only
    bus.subscribe("task.completed", on_task_cascade_notify)

    logger.info(
        "Ontology listeners registered: %d handlers", bus.handler_count()
    )
