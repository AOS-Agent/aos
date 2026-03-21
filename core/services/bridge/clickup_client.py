"""ClickUp API client for the bridge — task management from Telegram."""

import logging
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / "aos"
API_BASE = "https://api.clickup.com/api/v2"

# Space and List IDs
SPACES = {
    "aos": "90112322855",
    "nuchay": "90114112968",
}

LISTS = {
    "telegram": "901113377348",
    "project-mgmt": "901113377349",
    "dashboard": "901113377350",
    "infra": "901113377351",
    "nuchay-dev": "901113377352",
    "nuchay-ops": "901113377353",
}

# Default list for new tasks
DEFAULT_LIST = LISTS["telegram"]


def _get_token() -> str | None:
    try:
        result = subprocess.run(
            [str(WORKSPACE / "bin" / "agent-secret"), "get", "CLICKUP_API_TOKEN"],
            capture_output=True, text=True, timeout=5,
        )
        val = result.stdout.strip()
        return val if val and result.returncode == 0 else None
    except Exception:
        return None


def _headers() -> dict:
    token = _get_token()
    if not token:
        raise ValueError("CLICKUP_API_TOKEN not found in Keychain")
    return {"Authorization": token, "Content-Type": "application/json"}


def create_task(name: str, description: str = "", list_id: str = None,
                priority: int = 3) -> dict | None:
    """Create a task in ClickUp. Returns the task dict or None on failure."""
    lid = list_id or DEFAULT_LIST
    try:
        r = httpx.post(
            f"{API_BASE}/list/{lid}/task",
            headers=_headers(),
            json={"name": name, "description": description, "priority": priority},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"ClickUp create_task failed: {e}")
        return None


def get_tasks(list_id: str = None, statuses: list[str] = None) -> list[dict]:
    """Get tasks from a ClickUp list."""
    lid = list_id or DEFAULT_LIST
    try:
        params = {}
        if statuses:
            for i, s in enumerate(statuses):
                params[f"statuses[]"] = s
        r = httpx.get(
            f"{API_BASE}/list/{lid}/task",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("tasks", [])
    except Exception as e:
        logger.error(f"ClickUp get_tasks failed: {e}")
        return []


def get_all_tasks(space_id: str = None) -> list[dict]:
    """Get all tasks across all lists in a space (or default AOS space)."""
    sid = space_id or SPACES["aos"]
    all_tasks = []
    try:
        # Get all lists in the space
        r = httpx.get(
            f"{API_BASE}/space/{sid}/list",
            headers=_headers(),
            timeout=10,
        )
        r.raise_for_status()
        lists = r.json().get("lists", [])

        for lst in lists:
            tasks = get_tasks(list_id=lst["id"])
            for t in tasks:
                t["_list_name"] = lst["name"]
            all_tasks.extend(tasks)
    except Exception as e:
        logger.error(f"ClickUp get_all_tasks failed: {e}")
    return all_tasks


def update_task_status(task_id: str, status: str) -> bool:
    """Update a task's status."""
    try:
        r = httpx.put(
            f"{API_BASE}/task/{task_id}",
            headers=_headers(),
            json={"status": status},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ClickUp update_task failed: {e}")
        return False


def format_tasks_message(tasks: list[dict]) -> str:
    """Format tasks into a Telegram-friendly HTML message."""
    if not tasks:
        return "<i>No tasks found.</i>"

    # Group by status
    groups = {}
    for t in tasks:
        st = t.get("status", {}).get("status", "unknown")
        groups.setdefault(st, []).append(t)

    lines = []
    status_icons = {
        "complete": "✅",
        "closed": "✅",
        "in progress": "🔄",
        "to do": "⬜",
    }

    for status_name in ["to do", "in progress", "complete", "closed"]:
        if status_name not in groups:
            continue
        icon = status_icons.get(status_name, "⬜")
        lines.append(f"\n<b>{status_name.upper()}</b>")
        for t in groups[status_name]:
            name = t.get("name", "Untitled")
            list_name = t.get("_list_name", "")
            pri = t.get("priority", {})
            pri_label = f" P{pri.get('id', '?')}" if pri else ""
            lines.append(f"  {icon} {name}{pri_label}")
            if list_name:
                lines.append(f"      <i>{list_name}</i>")

    return "\n".join(lines) if lines else "<i>No tasks.</i>"
