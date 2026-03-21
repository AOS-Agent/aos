"""Evening check-in — sends structured reflection prompt at 9PM via Telegram."""

import datetime
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_ROOT = Path.home() / "vault"
STATE_FILE = Path.home() / ".aos" / "data" / "bridge" / "checkin_state.json"


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


def is_awaiting_checkin_reply() -> bool:
    """Check if we're within the reply window after sending a check-in."""
    state = _load_state()
    if not _already_sent_today(state):
        return False
    sent_ts = state.get("last_checkin_timestamp", 0)
    # 30-minute window to reply
    return (time.time() - sent_ts) < 1800


def mark_checkin_replied():
    """Mark that the check-in reply has been received."""
    state = _load_state()
    state["replied"] = True
    _save_state(state)


def was_checkin_replied() -> bool:
    state = _load_state()
    if not _already_sent_today(state):
        return False
    return state.get("replied", False)


def _build_checkin_message() -> str:
    return (
        "<b>Evening Check-in</b>\n\n"
        "How was your day? Reply with:\n\n"
        "<b>Energy</b> (1-5):\n"
        "<b>Sleep last night</b> (1-5):\n"
        "<b>One accomplishment:</b>\n"
        "<b>One thing for tomorrow:</b>\n\n"
        "<i>Example: 4, 3, shipped the vault integration, write the architecture doc</i>"
    )


def _save_checkin_to_daily(response_text: str):
    """Parse checkin response and update today's daily note."""
    today = datetime.date.today()
    daily_path = VAULT_ROOT / "daily" / f"{today.isoformat()}.md"

    # Parse response — expect: energy, sleep, accomplishment, tomorrow
    parts = [p.strip() for p in response_text.split(",", 3)]
    energy = parts[0] if len(parts) > 0 else ""
    sleep = parts[1] if len(parts) > 1 else ""
    accomplishment = parts[2] if len(parts) > 2 else ""
    tomorrow = parts[3] if len(parts) > 3 else ""

    if daily_path.exists():
        content = daily_path.read_text()
        # Update frontmatter fields if they exist
        if "energy:" in content and energy.strip().isdigit():
            content = content.replace(
                "energy:", f"energy: {energy.strip()}", 1
            )
        if "sleep:" in content and sleep.strip().isdigit():
            content = content.replace(
                "sleep:", f"sleep: {sleep.strip()}", 1
            )
        # Append to Evening Reflection section
        if "## Evening Reflection" in content:
            content = content.replace(
                "## Evening Reflection",
                f"## Evening Reflection\n\n"
                f"- **Energy**: {energy}\n"
                f"- **Sleep**: {sleep}\n"
                f"- **Accomplished**: {accomplishment}\n"
                f"- **Tomorrow**: {tomorrow}\n",
                1,
            )
        daily_path.write_text(content)
    else:
        # Create daily note from scratch
        content = (
            f"---\n"
            f"date: \"{today.isoformat()}\"\n"
            f"day: \"{today.strftime('%A')}\"\n"
            f"type: daily\n"
            f"energy: {energy.strip() if energy.strip().isdigit() else ''}\n"
            f"sleep: {sleep.strip() if sleep.strip().isdigit() else ''}\n"
            f"mood:\n"
            f"focus: \"\"\n"
            f"tags: [daily]\n"
            f"---\n\n"
            f"## Evening Reflection\n\n"
            f"- **Energy**: {energy}\n"
            f"- **Sleep**: {sleep}\n"
            f"- **Accomplished**: {accomplishment}\n"
            f"- **Tomorrow**: {tomorrow}\n"
        )
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        daily_path.write_text(content)

    logger.info(f"Evening check-in saved to {daily_path}")


def _send_checkin(bot_token: str, chat_id: int):
    """Send the evening check-in message via httpx (sync, no async needed)."""
    import httpx

    state = _load_state()
    if _already_sent_today(state):
        return

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = httpx.post(url, json={
            "chat_id": chat_id,
            "text": _build_checkin_message(),
            "parse_mode": "HTML",
        }, timeout=15)
        resp.raise_for_status()
        state["last_checkin_date"] = datetime.date.today().isoformat()
        state["last_checkin_timestamp"] = time.time()
        state["replied"] = False
        _save_state(state)
        logger.info("Evening check-in sent")
    except Exception as e:
        logger.error(f"Failed to send evening check-in: {e}")


def start_evening_checkin(bot_token: str, chat_id: int, hour: int = 21, minute: int = 0):
    """Start a background thread that sends evening check-in at the specified time."""

    def _loop():
        while True:
            now = datetime.datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            time.sleep(wait_seconds)

            _send_checkin(bot_token, chat_id)

            # Sleep to avoid double-fire
            time.sleep(120)

    thread = threading.Thread(target=_loop, daemon=True, name="evening-checkin")
    thread.start()
    logger.info(f"Evening check-in scheduled for {hour:02d}:{minute:02d}")
