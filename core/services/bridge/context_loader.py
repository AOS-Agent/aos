"""Context loader — gathers recent context to prepend to Claude sessions.

Collects: today's daily note summary, recent sessions, active tasks, pending items.
Returns a short context block (under 500 chars) that gets prepended to the user's message.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

AOS_DIR = Path.home() / "aos"
VAULT_DIR = Path.home() / "vault"
DAILY_DIR = VAULT_DIR / "daily"
SESSIONS_DIR = VAULT_DIR / "sessions"
PENDING_RULES = AOS_DIR / "apps" / "bridge" / "data" / "bridge" / "pending_rules.json"


def load_context() -> str | None:
    """Gather recent context. Returns a short block or None if nothing useful."""
    parts = []

    # 1. Today's daily note — energy, sleep, yesterday's reflection
    daily = _get_daily_note()
    if daily:
        parts.append(daily)

    # 2. Recent sessions — what was worked on in last 24h
    sessions = _get_recent_sessions()
    if sessions:
        parts.append(sessions)

    # 3. Active tasks
    tasks = _get_active_tasks()
    if tasks:
        parts.append(tasks)

    # 4. Pending items
    pending = _get_pending_items()
    if pending:
        parts.append(pending)

    if not parts:
        return None

    context = "\n".join(parts)

    # Keep it tight
    if len(context) > 600:
        context = context[:597] + "..."

    return f"<context>\n{context}\n</context>\n\n"


def _get_daily_note() -> str | None:
    """Extract key frontmatter from today's daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    note_path = DAILY_DIR / f"{today}.md"

    if not note_path.exists():
        return None

    try:
        text = note_path.read_text()
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not fm_match:
            return None

        fm = yaml.safe_load(fm_match.group(1))
        parts = []

        energy = fm.get("energy")
        sleep = fm.get("sleep")
        if energy:
            parts.append(f"energy={energy}/5")
        if sleep:
            parts.append(f"sleep={sleep}")

        # Check for evening reflection from yesterday
        tomorrow = fm.get("tomorrow")
        if tomorrow:
            parts.append(f"today's plan: {str(tomorrow)[:80]}")

        if not parts:
            return None

        return "Today: " + ", ".join(parts)
    except Exception as e:
        logger.debug(f"Daily note parse failed: {e}")
        return None


def _get_recent_sessions() -> str | None:
    """Get titles/projects of sessions from the last 24 hours."""
    if not SESSIONS_DIR.exists():
        return None

    cutoff = datetime.now() - timedelta(hours=24)
    recent = []

    for f in sorted(SESSIONS_DIR.glob("*.md"), reverse=True):
        try:
            text = f.read_text(errors="ignore")
            fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))

            date_str = str(fm.get("date", ""))
            time_str = str(fm.get("time", "00:00"))
            if not date_str:
                continue

            session_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            if session_dt < cutoff:
                continue

            project = fm.get("project", "")
            # Get first line of summary
            summary_match = re.search(r"## Summary\n\n(.+)", text)
            summary = summary_match.group(1)[:60] if summary_match else f.stem[:30]

            recent.append(f"{project}: {summary}")
        except Exception:
            continue

        if len(recent) >= 3:
            break

    if not recent:
        return None

    return "Recent work: " + " | ".join(recent)


def _get_active_tasks() -> str | None:
    """Get active tasks — returns None (migrating to vault-based tasks)."""
    return None


def _get_pending_items() -> str | None:
    """Check for pending friction rules or other actionable items."""
    items = []

    if PENDING_RULES.exists():
        try:
            data = json.loads(PENDING_RULES.read_text())
            if data.get("status") == "pending":
                n = len(data.get("rules", []))
                items.append(f"{n} pending friction rule(s)")
        except Exception:
            pass

    if not items:
        return None

    return "Pending: " + ", ".join(items)
