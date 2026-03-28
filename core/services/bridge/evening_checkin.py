"""Evening wrap — conversational end-of-day message via Telegram.

Bridge v2 format: celebratory first (done today), then open items,
then an invitation to reflect. Not a form. Not a rating scale.

Sends to the `daily` forum topic if available.
"""

import datetime
import importlib.util
import json
import logging
import threading
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

VAULT_ROOT = Path.home() / "vault"
AOS_ROOT = Path.home() / "aos"
STATE_FILE = Path.home() / ".aos" / "data" / "bridge" / "checkin_state.json"
BRIDGE_TOPICS_FILE = Path.home() / ".aos" / "config" / "bridge-topics.yaml"
INITIATIVES_DIR = VAULT_ROOT / "knowledge" / "initiatives"


# ── Message splitting ────────────────────────────────────────────────────────

def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split text into Telegram-safe chunks. Imported from core/infra/lib/notify if
    available, otherwise uses this inline fallback."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


# Try to import the canonical _split_message from core/infra/lib/notify
try:
    _notify_spec = importlib.util.spec_from_file_location(
        "notify", str(AOS_ROOT / "core" / "infra" / "lib" / "notify.py"))
    if _notify_spec:
        _notify_mod = importlib.util.module_from_spec(_notify_spec)
        _notify_spec.loader.exec_module(_notify_mod)
        _split_message = _notify_mod._split_message  # noqa: F811
except Exception:
    pass  # fallback above is fine


# ── Work engine (dynamic import) ─────────────────────────────────────────────

def _load_work_engine():
    """Dynamically import the work engine. Returns the module or None."""
    try:
        engine_path = AOS_ROOT / "core" / "work" / "engine.py"
        spec = importlib.util.spec_from_file_location("engine", str(engine_path))
        if spec:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    except Exception as e:
        logger.debug(f"Could not load work engine: {e}")
    return None


# ── State management ─────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _already_sent_today(state: dict) -> bool:
    last = state.get("last_checkin_date")
    return last == datetime.date.today().isoformat()


# ── Reply tracking (kept for bridge routing) ─────────────────────────────────

def is_awaiting_checkin_reply() -> bool:
    """Check if we're within the reply window after sending an evening wrap."""
    state = _load_state()
    if not _already_sent_today(state):
        return False
    if state.get("replied", False):
        return False
    sent_ts = state.get("last_checkin_timestamp", 0)
    # 30-minute window to reply
    return (time.time() - sent_ts) < 1800


def mark_checkin_replied():
    """Mark that the evening wrap reply has been received."""
    state = _load_state()
    state["replied"] = True
    _save_state(state)


def was_checkin_replied() -> bool:
    state = _load_state()
    if not _already_sent_today(state):
        return False
    return state.get("replied", False)


# ── Daily thread_id lookup ───────────────────────────────────────────────────

def _get_daily_thread_id() -> int | None:
    """Read the daily topic thread_id from bridge-topics.yaml."""
    try:
        if BRIDGE_TOPICS_FILE.exists():
            data = yaml.safe_load(BRIDGE_TOPICS_FILE.read_text())
            if isinstance(data, dict):
                topics = data.get("topics", {})
                daily = topics.get("daily", {})
                if isinstance(daily, dict):
                    tid = daily.get("thread_id")
                    return int(tid) if tid is not None else None
    except Exception as e:
        logger.debug(f"Could not read daily thread_id: {e}")
    return None


# ── Initiative progress ──────────────────────────────────────────────────────

def _load_initiatives() -> list[dict]:
    """Read initiative docs from vault/knowledge/initiatives/*.md.

    Returns a list of dicts with 'title' and 'tags' (keywords) that can be
    matched against completed task titles/projects.
    """
    initiatives = []
    try:
        if not INITIATIVES_DIR.is_dir():
            return initiatives
        for md_file in INITIATIVES_DIR.glob("*.md"):
            content = md_file.read_text()
            title = md_file.stem.replace("-", " ").replace("_", " ").title()
            # Try to extract title from frontmatter or first heading
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
                if stripped.startswith("title:"):
                    title = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    break
            # Extract tags/keywords for matching
            tags = set()
            tags.add(md_file.stem.lower())
            for word in title.lower().split():
                if len(word) > 3:
                    tags.add(word)
            initiatives.append({"title": title, "tags": tags, "file": md_file.name})
    except Exception as e:
        logger.debug(f"Could not load initiatives: {e}")
    return initiatives


def _match_initiative(task: dict, initiatives: list[dict]) -> str | None:
    """Check if a completed task relates to any initiative. Returns initiative
    title or None."""
    task_text = (task.get("title", "") + " " + task.get("project", "")).lower()
    for init in initiatives:
        for tag in init["tags"]:
            if tag in task_text:
                return init["title"]
    return None


# ── Build the evening wrap ───────────────────────────────────────────────────

def _build_evening_wrap() -> str:
    """Build the conversational evening wrap message.

    Reads today's completed tasks and active/open tasks from the work engine.
    Checks for initiative progress. Formats as HTML.
    """
    today = datetime.date.today()
    day_name = today.strftime("%A")

    completed_tasks = []
    open_tasks = []
    initiative_hits = []

    engine = _load_work_engine()
    if engine:
        try:
            all_tasks = engine.get_all_tasks()
            today_str = today.isoformat()

            # Completed today: status=done and completed timestamp starts with today
            for t in all_tasks:
                completed_ts = t.get("completed", "")
                if (t.get("status") == "done"
                        and isinstance(completed_ts, str)
                        and completed_ts.startswith(today_str)
                        and not t.get("parent")):
                    completed_tasks.append(t)

            # Also include subtasks completed today (show parent context)
            for t in all_tasks:
                completed_ts = t.get("completed", "")
                if (t.get("status") == "done"
                        and isinstance(completed_ts, str)
                        and completed_ts.startswith(today_str)
                        and t.get("parent")):
                    # Find parent title for context
                    parent_title = None
                    for pt in all_tasks:
                        if pt["id"] == t["parent"]:
                            parent_title = pt.get("title")
                            break
                    t["_parent_title"] = parent_title
                    completed_tasks.append(t)

            # Open tasks: active/todo/in-progress, not subtasks
            for t in all_tasks:
                if (t.get("status") in ("active", "todo", "in-progress")
                        and not t.get("parent")):
                    open_tasks.append(t)

            # Check initiative progress
            initiatives = _load_initiatives()
            if initiatives:
                seen_inits = set()
                for t in completed_tasks:
                    match = _match_initiative(t, initiatives)
                    if match and match not in seen_inits:
                        seen_inits.add(match)
                        initiative_hits.append(match)

        except Exception as e:
            logger.error(f"Failed to read tasks for evening wrap: {e}")

    # ── Format the message ────────────────────────────────────────────────
    lines = []
    lines.append(f"\U0001f319 <b>Wrapping up {day_name}</b>\n")

    # Done today (celebratory first)
    if completed_tasks:
        lines.append("\u2705 <b>Done today:</b>")
        for t in completed_tasks:
            title = t.get("title", "Untitled")
            project = t.get("project", "")
            parent_title = t.get("_parent_title")
            if parent_title:
                lines.append(f"  \u2022 {title} <i>(under {parent_title})</i>")
            elif project:
                lines.append(f"  \u2022 {title} <i>({project})</i>")
            else:
                lines.append(f"  \u2022 {title}")
    else:
        lines.append("\u2705 <b>Done today:</b>")
        lines.append("  \u2022 <i>No tasks completed today.</i>")

    # Initiative progress
    if initiative_hits:
        lines.append("")
        lines.append("  \U0001f4c8 <i>Progress on: " + ", ".join(initiative_hits) + "</i>")

    lines.append("")

    # Still open
    if open_tasks:
        lines.append("\U0001f4cb <b>Still open:</b>")
        for t in open_tasks[:8]:
            title = t.get("title", "Untitled")
            project = t.get("project", "")
            status = t.get("status", "")
            status_indicator = ""
            if status == "active" or status == "in-progress":
                status_indicator = " \u2014 <i>in progress</i>"
            if project:
                lines.append(f"  \u2022 {title} <i>({project})</i>{status_indicator}")
            else:
                lines.append(f"  \u2022 {title}{status_indicator}")
        if len(open_tasks) > 8:
            lines.append(f"  \u2022 <i>...and {len(open_tasks) - 8} more</i>")
    else:
        lines.append("\U0001f4cb <b>Still open:</b>")
        lines.append("  \u2022 <i>Plate is clear.</i>")

    lines.append("")

    # Invitation — conversational, not a form
    lines.append("Anything you want me to remember?")
    lines.append("Anything I should work on overnight?")
    lines.append("Anything on your mind before tomorrow?")

    return "\n".join(lines)


# ── Send the evening wrap ────────────────────────────────────────────────────

def _send_evening_wrap(bot_token: str, chat_id: int, thread_id: int | None = None):
    """Send the evening wrap message via Telegram.

    Sends to the daily forum topic if thread_id is provided.
    Uses message splitting if the message exceeds 4096 chars.
    """
    import httpx

    state = _load_state()
    if _already_sent_today(state):
        return

    try:
        text = _build_evening_wrap()
        chunks = _split_message(text)

        for chunk in chunks:
            if not chunk.strip():
                continue
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }
            if thread_id:
                payload["message_thread_id"] = thread_id

            resp = httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()

        # Update state
        state["last_checkin_date"] = datetime.date.today().isoformat()
        state["last_checkin_timestamp"] = time.time()
        state["replied"] = False
        _save_state(state)
        logger.info("Evening wrap sent" + (f" (thread_id={thread_id})" if thread_id else ""))

    except Exception as e:
        logger.error(f"Failed to send evening wrap: {e}")


# ── Save checkin response to daily note ──────────────────────────────────────

def _save_checkin_to_daily(response_text: str):
    """Save the operator's free-form evening response to today's daily note.

    No parsing, no structure enforcement. Whatever they said goes into an
    Evening Reflection section as-is.
    """
    try:
        today = datetime.date.today()
        daily_path = VAULT_ROOT / "daily" / f"{today.isoformat()}.md"

        # Clean up the response — strip leading/trailing whitespace
        reflection = response_text.strip()
        if not reflection:
            return

        timestamp = datetime.datetime.now().strftime("%H:%M")

        if daily_path.exists():
            content = daily_path.read_text()

            # If there's already an Evening Reflection section, append to it
            if "## Evening Reflection" in content:
                # Append under the existing section
                content = content.replace(
                    "## Evening Reflection",
                    f"## Evening Reflection\n\n"
                    f"*{timestamp}*\n"
                    f"{reflection}\n",
                    1,
                )
            else:
                # Add the section at the end
                content = content.rstrip() + (
                    f"\n\n## Evening Reflection\n\n"
                    f"*{timestamp}*\n"
                    f"{reflection}\n"
                )
            daily_path.write_text(content)
        else:
            # Create a minimal daily note with the reflection
            content = (
                f"---\n"
                f"date: \"{today.isoformat()}\"\n"
                f"day: \"{today.strftime('%A')}\"\n"
                f"type: daily\n"
                f"tags: [daily]\n"
                f"---\n\n"
                f"## Evening Reflection\n\n"
                f"*{timestamp}*\n"
                f"{reflection}\n"
            )
            daily_path.parent.mkdir(parents=True, exist_ok=True)
            daily_path.write_text(content)

        logger.info(f"Evening reflection saved to {daily_path}")

    except Exception as e:
        logger.error(f"Failed to save evening reflection: {e}")


# ── Scheduler ────────────────────────────────────────────────────────────────

def start_evening_checkin(bot_token: str, chat_id: int, hour: int = 21, minute: int = 0,
                          forum_group_id: int | None = None):
    """Start a background thread that sends the evening wrap at the specified time.

    Looks up the daily forum topic thread_id from bridge-topics.yaml so the
    message lands in the right topic. Falls back to DM if no topic exists.

    Args:
        bot_token: Telegram bot token
        chat_id: Telegram DM chat ID (fallback)
        hour: Hour to send (24h format)
        minute: Minute to send
        forum_group_id: Telegram forum group ID for topic routing
    """

    def _loop():
        while True:
            now = datetime.datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            time.sleep(wait_seconds)

            # Look up the daily topic thread_id each time (it may be created later)
            thread_id = _get_daily_thread_id()

            # Route to forum group if thread exists, otherwise DM
            target_chat = forum_group_id if (thread_id and forum_group_id) else chat_id
            _send_evening_wrap(bot_token, target_chat, thread_id=thread_id)

            # Sleep to avoid double-fire
            time.sleep(120)

    thread = threading.Thread(target=_loop, daemon=True, name="evening-checkin")
    thread.start()
    logger.info(f"Evening wrap scheduled for {hour:02d}:{minute:02d}")
