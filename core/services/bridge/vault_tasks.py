"""Vault-based task management — reads/writes ~/vault/tasks/*.md files.

Replaces plane_client.py. Tasks are markdown files with YAML frontmatter.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

VAULT_TASKS = Path.home() / "vault" / "tasks"
VAULT_TASKS.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR = VAULT_TASKS / "archive"


def _parse_task(path: Path) -> dict | None:
    """Parse a task markdown file into a dict."""
    try:
        content = path.read_text()
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        meta = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        meta["_file"] = path.name
        meta["_path"] = str(path)
        meta["_body"] = body
        return meta
    except Exception as e:
        logger.debug(f"Failed to parse {path}: {e}")
        return None


def get_all_tasks() -> list[dict]:
    """Get all active tasks (non-archived)."""
    tasks = []
    for f in sorted(VAULT_TASKS.glob("*.md")):
        task = _parse_task(f)
        if task:
            tasks.append(task)
    return tasks


def get_tasks_by_status(*statuses: str) -> list[dict]:
    """Get tasks filtered by status(es)."""
    return [t for t in get_all_tasks() if t.get("status") in statuses]


def get_active_tasks() -> list[dict]:
    """Get non-done, non-archived tasks."""
    return [t for t in get_all_tasks() if t.get("status") not in ("done",)]


def get_focus_tasks() -> list[dict]:
    """Get tasks with status=focus."""
    return get_tasks_by_status("focus")


def get_waiting_tasks() -> list[dict]:
    """Get tasks with status=waiting."""
    return get_tasks_by_status("waiting")


def create_task(title: str, domain: str = "aos", priority: int = 3,
                status: str = "backlog", created_by: str = "operator",
                due: str = None) -> Path:
    """Create a new task file in the vault."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().strip())[:60].strip("-")
    path = VAULT_TASKS / f"{slug}.md"

    # Avoid overwrites
    if path.exists():
        slug = f"{slug}-{datetime.now().strftime('%H%M')}"
        path = VAULT_TASKS / f"{slug}.md"

    frontmatter = {
        "title": title,
        "status": status,
        "priority": priority,
        "domain": domain,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "created_by": created_by,
    }
    if due:
        frontmatter["due"] = due

    content = "---\n" + yaml.dump(frontmatter, default_flow_style=False).strip() + "\n---\n"
    path.write_text(content)
    logger.info(f"Created task: {path.name}")
    return path


def update_task_status(title_search: str, new_status: str) -> dict | None:
    """Find a task by partial title match and update its status."""
    search = title_search.lower()
    for task in get_all_tasks():
        if search in task.get("title", "").lower():
            path = Path(task["_path"])
            content = path.read_text()
            # Replace status in frontmatter
            content = re.sub(
                r"^status:\s*\S+",
                f"status: {new_status}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            # Add completed date if marking done
            if new_status == "done" and "completed:" not in content:
                content = content.replace(
                    f"status: {new_status}",
                    f"status: {new_status}\ncompleted: {datetime.now().strftime('%Y-%m-%d')}",
                    1,
                )
            path.write_text(content)
            logger.info(f"Updated {path.name} → {new_status}")
            task["status"] = new_status
            return task
    return None


def archive_done_tasks(days_old: int = 14) -> int:
    """Move done tasks older than N days to archive/."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = 0
    now = datetime.now()
    for task in get_tasks_by_status("done"):
        completed = task.get("completed")
        if completed:
            try:
                completed_date = datetime.strptime(str(completed), "%Y-%m-%d")
                if (now - completed_date).days >= days_old:
                    src = Path(task["_path"])
                    dst = ARCHIVE_DIR / src.name
                    src.rename(dst)
                    archived += 1
            except (ValueError, TypeError):
                pass
    return archived


def format_tasks_telegram(tasks: list[dict]) -> str:
    """Format tasks for Telegram HTML output."""
    if not tasks:
        return "<i>No tasks.</i>"

    # Group by status
    groups = {}
    for t in tasks:
        status = t.get("status", "backlog")
        groups.setdefault(status, []).append(t)

    icons = {
        "focus": "🎯",
        "in-progress": "🔄",
        "todo": "⬜",
        "waiting": "⏳",
        "backlog": "📋",
        "done": "✅",
        "blocked": "🚫",
    }

    # Display order
    order = ["focus", "in-progress", "todo", "waiting", "backlog", "done"]
    lines = []

    for status in order:
        if status not in groups:
            continue
        icon = icons.get(status, "⬜")
        lines.append(f"\n<b>{status.upper()}</b>")
        for t in sorted(groups[status], key=lambda x: x.get("priority", 9)):
            title = t.get("title", "Untitled")
            domain = t.get("domain", "")
            priority = t.get("priority", "")
            p_str = f" P{priority}" if priority else ""
            d_str = f" <i>({domain})</i>" if domain else ""
            waiting = ""
            if status == "waiting" and t.get("waiting_on"):
                waiting = f" — waiting on {t['waiting_on']}"
            lines.append(f"  {icon} {title}{p_str}{d_str}{waiting}")

    return "\n".join(lines) if lines else "<i>No tasks.</i>"
