"""Daily briefing — BLUF morning scan delivered via Telegram.

Bridge v2 format: Bottom Line Up Front. Scannable in 10 seconds.
No system metrics, no trust scores, no session counts. Delta only.

Runs once per day at a configured hour. Scans tasks, initiatives,
schedule, and overnight work, then sends a classified briefing.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / "aos"
VAULT = Path.home() / "vault"
INITIATIVES_DIR = VAULT / "knowledge" / "initiatives"
OPERATOR_CONFIG = Path.home() / ".aos" / "config" / "operator.yaml"

# Max items per BLUF section (Cowan 2001 cognitive load)
MAX_ITEMS = 4


# ── Config helpers ─────────────────────────────────────────────────────


def _get_config() -> tuple[str, int, int]:
    """Return (timezone, briefing_hour, briefing_minute) from goals.yaml."""
    goals_path = WORKSPACE / "config" / "goals.yaml"
    if goals_path.exists():
        data = yaml.safe_load(goals_path.read_text())
        wh = data.get("work_hours", {}) if data else {}
        tz = wh.get("timezone", "America/Toronto")
        return tz, 8, 0
    return "America/Toronto", 8, 0


def _load_yaml(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text()) or {}
    return {}


# ── Triage helpers ─────────────────────────────────────────────────────

TRIAGE_FILE = Path.home() / ".aos" / "work" / "triage-state.json"


def _load_triage_unanswered() -> list[dict]:
    """Load unanswered messages from triage state, sorted oldest first."""
    try:
        if TRIAGE_FILE.exists():
            state = json.loads(TRIAGE_FILE.read_text())
            entries = list(state.get("unanswered", {}).values())
            # Sort by received_at ascending (oldest first)
            entries.sort(key=lambda e: e.get("received_at", ""))
            return entries
    except Exception:
        pass
    return []


def _time_ago(iso_ts: str) -> str:
    """Convert ISO timestamp to relative time string like '2h ago', '3d ago'."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        now = datetime.now(dt.tzinfo)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        return f"{weeks}w ago"
    except Exception:
        return "recently"


# ── Initiative scanner ─────────────────────────────────────────────────


def _scan_initiatives() -> list[dict]:
    """Scan vault/knowledge/initiatives/*.md for active initiatives.

    Parses YAML frontmatter using find() (not index()) for safety.
    Returns list of dicts with: title, status, phase, total_phases,
    updated, stale (bool).
    """
    results = []
    if not INITIATIVES_DIR.exists():
        return results

    tz_name, _, _ = _get_config()
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    # Read stale threshold from operator config
    op = _load_yaml(OPERATOR_CONFIG)
    stale_days = 3
    init_cfg = op.get("initiatives", {})
    if isinstance(init_cfg, dict):
        stale_days = init_cfg.get("stale_threshold_days", 3)

    for md_path in sorted(INITIATIVES_DIR.glob("*.md")):
        try:
            content = md_path.read_text()
            if not content.startswith("---"):
                continue

            # Find the closing --- of frontmatter
            end = content.find("---", 3)
            if end == -1:
                continue

            frontmatter = content[3:end]
            meta = yaml.safe_load(frontmatter)
            if not isinstance(meta, dict):
                continue

            status = meta.get("status", "")
            # Skip done/archived
            if status in ("done", "archived"):
                continue

            title = meta.get("title", md_path.stem)
            phase = meta.get("phase")
            total_phases = meta.get("total_phases")
            updated_raw = meta.get("updated")

            # Determine staleness
            stale = False
            updated_str = ""
            if updated_raw:
                try:
                    if isinstance(updated_raw, str):
                        updated_date = datetime.strptime(updated_raw, "%Y-%m-%d").replace(tzinfo=tz)
                    else:
                        # date object from YAML
                        updated_date = datetime.combine(updated_raw, datetime.min.time()).replace(tzinfo=tz)
                    days_since = (now - updated_date).days
                    stale = days_since > stale_days
                    updated_str = updated_raw if isinstance(updated_raw, str) else str(updated_raw)
                except (ValueError, TypeError):
                    updated_str = str(updated_raw)

            results.append({
                "title": title,
                "status": status,
                "phase": phase,
                "total_phases": total_phases,
                "updated": updated_str,
                "stale": stale,
                "file": md_path.name,
            })
        except Exception as e:
            logger.debug(f"Failed to parse initiative {md_path.name}: {e}")

    return results


# ── BLUF briefing builder ─────────────────────────────────────────────


def _build_briefing() -> str:
    """Build the BLUF daily briefing.

    Sections: URGENT, IMPORTANT, THINK ABOUT, PEOPLE, OVERNIGHT.
    No system metrics, no trust scores — delta only.
    """
    tz_name, _, _ = _get_config()
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    urgent = []      # Things needing action TODAY
    important = []   # Things to move forward this week
    think = []       # Open threads, unresolved decisions
    people = []      # Follow-ups, waiting, meetings
    overnight = []   # Work done overnight

    # ── Load tasks from work engine ──────────────────────────────────
    work_tasks = []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "engine", str(Path.home() / "aos" / "core" / "work" / "engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        work_tasks = mod.get_all_tasks()
    except Exception as e:
        logger.debug(f"Work engine unavailable: {e}")

    # vault_tasks fallback removed — work engine is the single source of truth

    # ── URGENT: overdue tasks, stale initiatives, P1 active ──────────
    today_str = now.strftime("%Y-%m-%d")

    # Overdue tasks (work engine)
    for t in work_tasks:
        if t.get("status") in ("done", "archived"):
            continue
        due = t.get("due")
        if due:
            try:
                due_str = str(due).split("T")[0]
                if due_str < today_str:
                    days_late = (now.date() - datetime.strptime(due_str, "%Y-%m-%d").date()).days
                    urgent.append(f"<b>{t['title']}</b> — overdue by {days_late}d")
            except (ValueError, TypeError):
                pass

    # vault_tasks overdue check removed — work engine handles all tasks

    # Stale initiatives → URGENT
    initiatives = _scan_initiatives()
    for init in initiatives:
        if init["stale"]:
            phase_info = ""
            if init.get("phase") and init.get("total_phases"):
                phase_info = f" (phase {init['phase']}/{init['total_phases']})"
            urgent.append(f"<b>{init['title']}</b> — stale, last updated {init['updated']}{phase_info}")

    # P1 active tasks
    for t in work_tasks:
        if t.get("priority") == 1 and t.get("status") in ("active", "todo", "in-progress", "focus"):
            if not t.get("parent"):  # Skip subtasks
                title = t.get("title", "Untitled")
                if not any(title in u for u in urgent):
                    urgent.append(f"<b>{title}</b> — P1 active")

    # ── IMPORTANT: active initiatives, high-pri tasks, due this week ─
    # Active initiatives with phase info
    for init in initiatives:
        if not init["stale"] and init["status"] in ("executing", "planning"):
            phase_info = ""
            if init.get("phase") and init.get("total_phases"):
                phase_info = f" — phase {init['phase']}/{init['total_phases']}"
            important.append(f"<b>{init['title']}</b> [{init['status']}]{phase_info}")

    # High priority todo tasks (P2)
    for t in work_tasks:
        if t.get("status") in ("active", "todo", "focus", "in-progress") and not t.get("parent"):
            if t.get("priority") == 2:
                important.append(f"{t['title']} — P2")

    # Tasks due this week
    week_end = now + timedelta(days=(6 - now.weekday()))  # End of this week (Sunday)
    for t in work_tasks:
        if t.get("status") in ("done", "archived"):
            continue
        due = t.get("due")
        if due:
            try:
                due_str = str(due).split("T")[0]
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
                if due_date >= now.date() and due_date <= week_end.date():
                    title = t.get("title", "Untitled")
                    if not any(title in item for item in important) and not any(title in item for item in urgent):
                        important.append(f"{title} — due {due_str}")
            except (ValueError, TypeError):
                pass

    # ── THINK ABOUT: research/shaping initiatives, inbox, open threads ─
    # Initiatives in research/shaping
    for init in initiatives:
        if not init["stale"] and init["status"] in ("research", "shaping"):
            think.append(f"<b>{init['title']}</b> [{init['status']}] — needs input")

    # Inbox items awaiting triage
    try:
        if work_tasks:
            # Already loaded the engine above
            data = mod._load()
            inbox_items = data.get("inbox", [])
            if len(inbox_items) > 3:
                think.append(f"{len(inbox_items)} inbox items awaiting triage")
            elif inbox_items:
                for item in inbox_items[:MAX_ITEMS]:
                    text = item.get("text", item) if isinstance(item, dict) else str(item)
                    think.append(f"Inbox: {str(text)[:60]}")
    except Exception:
        pass

    # Open threads with no recent activity
    try:
        if work_tasks:
            data = mod._load()
            threads = data.get("threads", [])
            for th in threads:
                if th.get("status", "open") == "open":
                    title = th.get("title", "Unnamed thread")
                    think.append(f"Open thread: {title}")
    except Exception:
        pass

    # ── PEOPLE: waiting tasks, schedule blocks ───────────────────────
    # Waiting tasks
    for t in work_tasks:
        if t.get("status") == "waiting":
            who = t.get("waiting_on", "someone")
            people.append(f"Waiting on <b>{who}</b>: {t.get('title', 'Untitled')}")

    # vault_tasks waiting check removed — work engine handles all tasks

    # Schedule blocks from operator.yaml
    op = _load_yaml(OPERATOR_CONFIG)
    schedule = op.get("schedule", {})
    blocks = schedule.get("blocks", [])
    day_abbrev = now.strftime("%a").lower()  # mon, tue, etc.
    for block in blocks:
        days = block.get("days", [])
        if day_abbrev in days:
            name = block.get("name", "Block")
            start = block.get("start", "")
            end = block.get("end", "")
            people.append(f"<b>{name}</b> today {start}–{end}")

    # ── OVERNIGHT: tasks completed late night / early morning ────────
    yesterday = now - timedelta(days=1)
    cutoff_evening = yesterday.replace(hour=22, minute=0, second=0, microsecond=0)
    cutoff_morning = now.replace(hour=6, minute=0, second=0, microsecond=0)

    for t in work_tasks:
        if t.get("status") == "done":
            completed = t.get("completed")
            if completed:
                try:
                    if isinstance(completed, str):
                        # Try ISO format first
                        comp_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                        if comp_dt.tzinfo is None:
                            comp_dt = comp_dt.replace(tzinfo=tz)
                        else:
                            comp_dt = comp_dt.astimezone(tz)
                    else:
                        continue
                    if cutoff_evening <= comp_dt <= cutoff_morning:
                        overnight.append(f"Completed: <b>{t.get('title', 'Untitled')}</b>")
                except (ValueError, TypeError):
                    pass

    # ── Format the BLUF ──────────────────────────────────────────────
    lines = []

    # Header
    lines.append(f"\u2600\ufe0f <b>{now.strftime('%A')}, {now.strftime('%B %d')}</b>\n")

    # URGENT
    lines.append("\U0001f534 <b>URGENT</b>")
    if urgent:
        for item in urgent[:MAX_ITEMS]:
            lines.append(f"  \u2022 {item}")
    else:
        lines.append("  Nothing urgent today.")
    lines.append("")

    # IMPORTANT
    lines.append("\U0001f7e1 <b>IMPORTANT</b>")
    if important:
        for item in important[:MAX_ITEMS]:
            lines.append(f"  \u2022 {item}")
    else:
        lines.append("  Nothing flagged this week.")
    lines.append("")

    # THINK ABOUT
    lines.append("\U0001f4ad <b>THINK ABOUT</b>")
    if think:
        for item in think[:MAX_ITEMS]:
            lines.append(f"  \u2022 {item}")
    else:
        lines.append("  No open threads.")
    lines.append("")

    # PEOPLE
    lines.append("\U0001f465 <b>PEOPLE</b>")
    if people:
        for item in people[:MAX_ITEMS]:
            lines.append(f"  \u2022 {item}")
    else:
        lines.append("  No people items today.")
    lines.append("")

    # MESSAGES (only if unanswered)
    unanswered = _load_triage_unanswered()
    if unanswered:
        lines.append("\U0001f4ac <b>MESSAGES</b>")
        for entry in unanswered[:5]:
            name = entry.get("person_name", "Unknown")
            channel = entry.get("channel", "?")
            ago = _time_ago(entry.get("received_at", ""))
            preview = entry.get("text_preview", "")
            lines.append(f"  \U0001f4ac {name} ({channel}) — {ago}")
            if preview:
                lines.append(f"    {preview[:80]}")
        lines.append("")

    # OVERNIGHT (only if applicable)
    if overnight:
        lines.append("\U0001f319 <b>OVERNIGHT</b>")
        for item in overnight[:MAX_ITEMS]:
            lines.append(f"  \u2022 {item}")
        lines.append("")

    # ── Create today's daily note if missing ─────────────────────────
    today_date_str = now.strftime("%Y-%m-%d")
    today_note = VAULT / "daily" / f"{today_date_str}.md"
    if not today_note.exists():
        template = VAULT / "templates" / "daily.md"
        if template.exists():
            try:
                content = template.read_text()
                content = content.replace("{{date}}", today_date_str)
                content = content.replace("{{day}}", now.strftime("%A"))
                today_note.parent.mkdir(parents=True, exist_ok=True)
                today_note.write_text(content)
                logger.info(f"Created daily note: {today_note}")
            except Exception as e:
                logger.debug(f"Failed to create daily note: {e}")

    return "\n".join(lines)


# ── Send helpers ───────────────────────────────────────────────────────


def _split_for_telegram(text: str, limit: int = 4096) -> list[str]:
    """Split text at paragraph/newline boundaries for Telegram's limit.

    Imports the canonical splitter from core/infra/lib/notify.py when possible,
    falls back to a local implementation.
    """
    if len(text) <= limit:
        return [text]

    try:
        import sys
        sys.path.insert(0, str(Path.home() / "aos" / "core" / "infra" / "lib"))
        from notify import _split_message
        return _split_message(text, limit)
    except Exception:
        pass

    # Fallback: split at double newlines, then single newlines
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def _get_daily_thread_id(bot_token: str, forum_group_id: int) -> int | None:
    """Get the daily topic thread_id from topic_manager."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "aos" / "core" / "services" / "bridge"))
        from topic_manager import TopicManager
        tm = TopicManager(bot_token, forum_group_id)
        return tm.get_topic_thread_id("daily")
    except Exception as e:
        logger.debug(f"Could not get daily thread_id: {e}")
        return None


def _send_briefing(bot_token: str, chat_id: int, thread_id: int | None = None):
    """Build and send the BLUF daily briefing via Telegram.

    Supports message splitting for >4096 char briefings and
    optional thread_id for the daily forum topic.
    """
    try:
        text = _build_briefing()
        chunks = _split_for_telegram(text)

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

            httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
                timeout=10,
            )

        logger.info("Daily briefing sent (BLUF format)")
    except Exception as e:
        logger.error(f"Daily briefing failed: {e}")


# ── Morning prompt (KEPT AS-IS) ───────────────────────────────────────


def _build_morning_prompt() -> str:
    """Build a personalized morning prompt that invites a voice note.

    Selects the right template based on: day of week, recent activity,
    time since last ramble, and active task count.
    """
    tz_name, _, _ = _get_config()
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    # Load operator name
    op_name = "there"
    op_file = Path.home() / ".aos" / "config" / "operator.yaml"
    if op_file.exists():
        op = yaml.safe_load(op_file.read_text()) or {}
        op_name = op.get("name", "there")

    # Count active tasks and recent completions
    active_count = 0
    done_yesterday = 0
    top_task = ""
    top_project = ""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "engine", str(Path.home() / "aos" / "core" / "work" / "engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tasks = mod.get_all_tasks()
        active = [t for t in tasks if t.get("status") in ("active", "todo") and not t.get("parent")]
        active_count = len(active)
        if active:
            top_task = active[0].get("title", "")
            top_project = active[0].get("project", "")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        done_yesterday = sum(1 for t in tasks
                             if t.get("status") == "done"
                             and t.get("completed", "").startswith(yesterday))
    except Exception:
        pass

    # Check last ramble date
    vault = Path.home() / "vault"
    gap_days = 0
    for d in range(1, 8):
        day_str = (now - timedelta(days=d)).strftime("%Y-%m-%d")
        note = vault / "daily" / f"{day_str}.md"
        if note.exists() and "ramble" in note.read_text().lower():
            gap_days = d
            break
    if gap_days == 0:
        gap_days = 7  # No recent ramble found

    day_of_week = now.strftime("%A")
    is_monday = day_of_week == "Monday"
    is_friday = day_of_week == "Friday"

    # Select prompt based on context
    if is_friday:
        prompts = [
            f"Asalamualaikum {op_name}. Jumuah mubarak.\n\n"
            f"Before the day \u2014 what are you grateful for this week? And what's one thing "
            f"you want to carry into next week?\n\n"
            f"Send a voice note. I'll capture it.",

            f"{op_name}, it's Friday. The week is almost wrapped.\n\n"
            f"What worked this week? What didn't? What would you do differently?\n\n"
            f"Just talk \u2014 I'll sort it out.",
        ]
    elif is_monday or gap_days > 3:
        prompts = [
            f"Asalamualaikum {op_name}. New week.\n\n"
            f"What are the 2-3 things that matter most this week? "
            f"Don't overthink it \u2014 just talk for a minute.\n\n"
            f"Send me a voice note.",

            f"Asalamualaikum {op_name}. It's been {gap_days} days since we last talked.\n\n"
            f"What happened? What changed? What needs your attention today?\n\n"
            f"Voice note \u2014 just ramble. I'll organize it.",
        ]
    elif done_yesterday >= 2:
        prompts = [
            f"Asalamualaikum {op_name}. You finished {done_yesterday} tasks yesterday. "
            f"That's momentum.\n\n"
            f"What do you want to keep moving today? What's the one thing that would "
            f"make today count?\n\n"
            f"Send a voice note.",

            f"{op_name} \u2014 productive day yesterday. {done_yesterday} things done.\n\n"
            f"What's the priority today? Talk to me \u2014 I'll turn it into tasks.",
        ]
    elif active_count > 3:
        prompts = [
            f"Asalamualaikum {op_name}. You've got {active_count} things in motion"
            + (f" \u2014 '{top_task}' is the most recent" if top_task else "") + ".\n\n"
            "What's actually important today? Not everything \u2014 just the real priorities.\n\n"
            "Voice note. 60 seconds. Go.",

            f"{op_name}, there's a lot on your plate \u2014 {active_count} active items.\n\n"
            f"What would make today feel like progress? What can wait?\n\n"
            f"Send a voice note and I'll help you sort it.",
        ]
    elif active_count == 0:
        prompts = [
            f"Asalamualaikum {op_name}. Your plate is clear right now.\n\n"
            f"What do you want this machine working on? What's been in the back "
            f"of your mind that you haven't started yet?\n\n"
            f"Send a voice note \u2014 even 30 seconds is enough.",

            f"{op_name}, nothing active right now. That's either peaceful or "
            f"something is being avoided.\n\n"
            f"What should we be working on? Talk to me.",
        ]
    else:
        prompts = [
            f"Asalamualaikum {op_name}. What's on your mind this morning?\n\n"
            + (f"'{top_task}' is still active" + (f" in {top_project}" if top_project else "") + ". " if top_task else "")
            + "Where do you want to push today?\n\n"
            "Send a voice note \u2014 I'll turn it into tasks and notes.",

            f"Morning {op_name}. Before the day takes over \u2014 "
            f"what matters most right now?\n\n"
            f"Not the urgent stuff. The important stuff.\n\n"
            f"Voice note. I'm listening.",
        ]

    import random
    return random.choice(prompts)


def _send_morning_prompt(bot_token: str, chat_id: int, thread_id: int | None = None):
    """Send the personalized morning prompt inviting a voice note."""
    try:
        text = _build_morning_prompt()
        payload = {"chat_id": chat_id, "text": text}
        if thread_id:
            payload["message_thread_id"] = thread_id

        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=10,
        )
        logger.info("Morning prompt sent")
    except Exception as e:
        logger.error(f"Morning prompt failed: {e}")


# ── Learning drip (KEPT AS-IS) ────────────────────────────────────────


def _send_learning_drip(bot_token: str, chat_id: int, time_slot: str):
    """Send the day's learning drip message if one is due.

    Reads onboarding completion date to determine which day they're on,
    then sends the appropriate tip for this time slot.
    """
    try:
        onboarding_file = Path.home() / ".aos" / "config" / "onboarding.yaml"
        if not onboarding_file.exists():
            return

        onboarding = yaml.safe_load(onboarding_file.read_text()) or {}
        completed_str = onboarding.get("completed", "")
        if not completed_str:
            return

        completed_date = datetime.fromisoformat(completed_str.replace("Z", "+00:00")).date()
        today = datetime.now().date()
        day_number = (today - completed_date).days + 1

        if day_number > 7:
            return

        drip_state_file = WORKSPACE / "data" / "bridge" / "drip_state.txt"
        drip_state_file.parent.mkdir(parents=True, exist_ok=True)
        drip_key = f"{today}:{time_slot}"
        if drip_state_file.exists() and drip_key in drip_state_file.read_text():
            return

        drip_config = WORKSPACE / "config" / "learning-drip.yaml"
        if not drip_config.exists():
            return

        drips = yaml.safe_load(drip_config.read_text()) or {}
        today_drips = [d for d in drips.get("days", [])
                       if d.get("day") == day_number and d.get("time") == time_slot]

        for drip in today_drips:
            msg = drip.get("message", "").strip()
            if msg:
                httpx.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg},
                    timeout=10,
                )
                logger.info(f"Learning drip sent: day {day_number}, {time_slot}")

        with open(drip_state_file, "a") as f:
            f.write(drip_key + "\n")

    except Exception as e:
        logger.debug(f"Learning drip error: {e}")


# ── Scheduling loop ───────────────────────────────────────────────────


def start_daily_briefing(bot_token: str, chat_id: int, hour: int = 8,
                         minute: int = 0, thread_id: int | None = None,
                         forum_group_id: int | None = None):
    """Start the daily briefing as a daemon thread.

    Checks every 5 minutes if it's time to send. Sends once per day at the
    configured hour (default 8:00 AM in the operator's timezone).

    Args:
        bot_token: Telegram bot token
        chat_id: Telegram chat ID (DM fallback)
        hour: Hour to send (24h format)
        minute: Minute to send
        thread_id: Forum topic thread_id for the daily topic (optional).
                   If None, tries to resolve via topic_manager.
        forum_group_id: Telegram forum group ID. If provided with a valid
                        daily topic thread_id, briefings route to the forum
                        instead of the DM.
    """

    def _loop():
        nonlocal thread_id

        # Persist last_sent_date to survive restarts
        state_file = WORKSPACE / "data" / "bridge" / "briefing_state.txt"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        last_sent_date = None
        try:
            if state_file.exists():
                stored = state_file.read_text().strip()
                if stored:
                    from datetime import date as _date
                    last_sent_date = _date.fromisoformat(stored)
        except Exception:
            pass

        # Try to resolve daily thread_id if not provided
        if thread_id is None and forum_group_id:
            try:
                thread_id_resolved = _get_daily_thread_id(bot_token, forum_group_id)
                if thread_id_resolved:
                    thread_id = thread_id_resolved
            except Exception:
                pass

        while True:
            try:
                tz_name, _, _ = _get_config()
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)

                # Morning prompt: send at the configured hour
                prompt_sent = state_file.with_suffix(".prompt").exists() and \
                    state_file.with_suffix(".prompt").read_text().strip() == str(now.date())

                # Use forum group for topic-routed messages, DM for fallback
                target_chat = forum_group_id if (thread_id and forum_group_id) else chat_id

                if (now.hour == hour and now.minute >= minute and
                        last_sent_date != now.date() and not prompt_sent):
                    _send_morning_prompt(bot_token, target_chat, thread_id)
                    _send_learning_drip(bot_token, chat_id, "morning")
                    state_file.with_suffix(".prompt").write_text(str(now.date()))

                # Send briefing 15 min after prompt
                briefing_minute = minute + 15
                briefing_hour = hour + (1 if briefing_minute >= 60 else 0)
                briefing_minute = briefing_minute % 60
                if (now.hour >= briefing_hour and
                    (now.hour == briefing_hour and now.minute >= briefing_minute or now.hour > briefing_hour) and
                        last_sent_date != now.date()):
                    _send_briefing(bot_token, target_chat, thread_id)
                    last_sent_date = now.date()
                    state_file.write_text(str(last_sent_date))

                # Midday learning drip (12:00-12:30)
                if now.hour == 12 and now.minute < 30:
                    _send_learning_drip(bot_token, chat_id, "midday")

                # Evening learning drip (20:00-20:30)
                if now.hour == 20 and now.minute < 30:
                    _send_learning_drip(bot_token, chat_id, "evening")

            except Exception as e:
                logger.error(f"Daily briefing loop error: {e}")

            # Check every 5 minutes
            threading.Event().wait(300)

    thread = threading.Thread(target=_loop, daemon=True, name="daily-briefing")
    thread.start()
    logger.info(f"Daily briefing scheduled at {hour:02d}:{minute:02d}")
    return thread
