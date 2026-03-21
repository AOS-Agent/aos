"""Messaging bridge — Telegram + Slack + heartbeat."""

import logging
import signal
import subprocess
import sys
import threading
from pathlib import Path

import yaml

WORKSPACE = Path.home() / "aos"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("bridge")

# Suppress httpx polling noise — 99% of bridge.err.log is getUpdates INFO spam
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _get_secret(name: str) -> str | None:
    """Get a secret from the agent keychain."""
    try:
        result = subprocess.run(
            [str(WORKSPACE / "bin" / "agent-secret"), "get", name],
            capture_output=True, text=True, timeout=5,
        )
        val = result.stdout.strip()
        return val if val and result.returncode == 0 else None
    except Exception:
        return None


def _load_routes() -> tuple[int | None, dict]:
    """Build forum group ID and topic routes from config/projects.yaml.

    Returns (forum_group_id, topic_routes) where topic_routes maps
    thread_id -> {"cwd": "/path", "agent": "agent_name"}.
    """
    config_path = WORKSPACE / "config" / "projects.yaml"
    if not config_path.exists():
        logger.warning("config/projects.yaml not found — no topic routes loaded")
        return None, {}

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    projects = config.get("projects", {})
    forum_group_id = None
    topic_routes = {}

    for name, proj in projects.items():
        if not isinstance(proj, dict) or proj.get("status") != "active":
            continue

        tg = proj.get("telegram", {})
        if not tg:
            continue

        # Use the first forum_group_id we find (they should all be the same
        # if projects share one group, or we could support multiple groups later)
        gid = tg.get("forum_group_id")
        if gid and forum_group_id is None:
            forum_group_id = int(gid)

        topic_id = tg.get("forum_topic_id")
        if topic_id is None:
            continue

        # Resolve path (expand ~)
        raw_path = proj.get("path", f"~/{name}")
        resolved_path = str(Path(raw_path).expanduser())

        # Agent name: first agent in list, or project name as default
        agents = proj.get("agents", name)
        if agents == "auto" or agents is None:
            agent_name = name
        elif isinstance(agents, list) and agents:
            agent_name = agents[0]
        else:
            agent_name = name

        topic_routes[int(topic_id)] = {
            "cwd": resolved_path,
            "agent": agent_name,
        }

    # Also check non-project entries (like technician) that have telegram config
    for key, entry in config.items():
        if key in ("projects", "system") or not isinstance(entry, dict):
            continue
        tg = entry.get("telegram", {})
        if not tg:
            continue
        topic_id = tg.get("forum_topic_id")
        gid = tg.get("forum_group_id")
        if topic_id is None:
            continue
        if gid and forum_group_id is None:
            forum_group_id = int(gid)

        agents = entry.get("agents", [key])
        agent_name = agents[0] if isinstance(agents, list) and agents else key

        topic_routes[int(topic_id)] = {
            "cwd": str(WORKSPACE),  # system agents work in aos/
            "agent": agent_name,
        }

    logger.info(f"Loaded routes: group={forum_group_id}, topics={topic_routes}")
    return forum_group_id, topic_routes


def main():
    # Required: Telegram
    bot_token = _get_secret("TELEGRAM_BOT_TOKEN")
    chat_id_str = _get_secret("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id_str:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in keychain")
        sys.exit(1)

    chat_id = int(chat_id_str)

    # Start heartbeat
    from heartbeat import start_heartbeat
    start_heartbeat(bot_token, chat_id, interval_minutes=30)
    logger.info("Heartbeat started (30 min interval, active hours only)")

    # Start daily briefing (8:00 AM)
    from daily_briefing import start_daily_briefing
    start_daily_briefing(bot_token, chat_id, hour=8, minute=0)
    logger.info("Daily briefing scheduled at 08:00")

    # Start evening check-in (9:00 PM)
    from evening_checkin import start_evening_checkin
    start_evening_checkin(bot_token, chat_id, hour=21, minute=0)
    logger.info("Evening check-in scheduled at 21:00")

    # Optional: Slack
    slack_bot_token = _get_secret("SLACK_BOT_TOKEN")
    slack_app_token = _get_secret("SLACK_APP_TOKEN")
    slack_user_id = _get_secret("SLACK_ALLOWED_USER_ID")

    if slack_bot_token and slack_app_token and slack_user_id:
        from slack_channel import SlackChannel
        slack = SlackChannel(slack_bot_token, slack_app_token, slack_user_id)
        slack_thread = threading.Thread(target=slack.start, daemon=True, name="slack")
        slack_thread.start()
        logger.info("Slack channel started")
    else:
        logger.info("Slack not configured (set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_USER_ID to enable)")

    # Load forum topic routes from config/projects.yaml
    forum_group_id, topic_routes = _load_routes()

    # Telegram channel (main thread, blocking)
    from telegram_channel import TelegramChannel
    telegram = TelegramChannel(
        bot_token, chat_id,
        forum_group_id=forum_group_id,
        topic_routes=topic_routes,
    )

    # SIGHUP handler: reload routes without restarting
    def _reload_routes(signum, frame):
        logger.info("SIGHUP received — reloading routes from config/projects.yaml")
        try:
            new_group_id, new_routes = _load_routes()
            telegram.topic_routes = new_routes
            if new_group_id:
                telegram.forum_group_id = new_group_id
            logger.info(f"Routes reloaded: {len(new_routes)} topic(s)")
        except Exception as e:
            logger.error(f"Failed to reload routes: {e}")

    signal.signal(signal.SIGHUP, _reload_routes)

    logger.info(f"Starting Telegram channel (main thread, forum group: {forum_group_id})")
    telegram.start()


if __name__ == "__main__":
    main()
