"""Messaging bridge — Telegram + Slack + heartbeat."""

import atexit
import glob as _glob
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

from bridge_events import bridge_event

WORKSPACE = Path.home() / "aos"
RUNTIME_DIR = Path.home() / ".aos" / "services" / "bridge"
LOG_DIR = Path.home() / ".aos" / "logs"
PID_FILE = RUNTIME_DIR / "bridge.pid"

# ── Log rotation — 5MB max, keep 3 backups ─────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)

_formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# Rotating file handler (replaces unbounded stderr writes)
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "bridge.log",
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
)
_file_handler.setFormatter(_formatter)

# Still log to stderr for launchd visibility
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler, _stderr_handler],
)
logger = logging.getLogger("bridge")

# Suppress httpx polling noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ── PID lock — prevents two instances from running simultaneously ────────────

def _acquire_pid_lock():
    """Write PID file. If another instance is running, kill it and take over.

    During launchctl kickstart -k, the old process may still have a lingering
    Telegram polling connection. We kill it explicitly and wait for the
    connection to expire server-side before starting our own polling.
    """
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            # Process is alive — kill it so we can take over
            logger.info(f"Killing previous bridge instance (PID {old_pid})")
            os.kill(old_pid, signal.SIGTERM)
            # Wait for it to die (up to 5s)
            for _ in range(50):
                time.sleep(0.1)
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    break
            else:
                # Still alive after 5s — force kill
                try:
                    os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (ProcessLookupError, ValueError):
            logger.info("Removing stale PID file (old PID gone)")
        except PermissionError:
            logger.error("Another bridge instance appears to be running. Exiting.")
            sys.exit(1)

    # Grace period — let old Telegram polling connection expire server-side
    logger.info("Waiting 3s for old polling connections to expire...")
    time.sleep(3)

    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_pid_lock)
    logger.info(f"PID lock acquired: {os.getpid()}")


def _release_pid_lock():
    """Remove PID file on clean shutdown."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Secrets ──────────────────────────────────────────────────────────────────

def _get_secret(name: str) -> str | None:
    """Get a secret from the agent keychain."""
    try:
        result = subprocess.run(
            [str(WORKSPACE / "core" / "bin" / "agent-secret"), "get", name],
            capture_output=True, text=True, timeout=5,
        )
        val = result.stdout.strip()
        return val if val and result.returncode == 0 else None
    except Exception:
        return None


# ── Temp file cleanup — removes leaked media downloads ───────────────────────

def _start_temp_cleanup(interval_minutes: int = 60):
    """Periodically clean up stale tg_* temp files older than 1 hour."""
    import tempfile

    def _cleanup():
        while True:
            time.sleep(interval_minutes * 60)
            try:
                tmp_dir = tempfile.gettempdir()
                cutoff = time.time() - 3600  # 1 hour ago
                for pattern in ("tg_photo_*", "tg_video_*", "tg_doc_*",
                                "tg_vdoc_*", "tg_frames_*"):
                    for path in _glob.glob(os.path.join(tmp_dir, pattern)):
                        try:
                            if os.path.getmtime(path) < cutoff:
                                if os.path.isdir(path):
                                    import shutil
                                    shutil.rmtree(path, ignore_errors=True)
                                else:
                                    os.unlink(path)
                        except OSError:
                            pass
            except Exception as e:
                logger.debug(f"Temp cleanup error: {e}")

    t = threading.Thread(target=_cleanup, daemon=True, name="temp-cleanup")
    t.start()


def _load_routes() -> tuple[int | None, dict]:
    """Build forum group ID and topic routes from config/projects.yaml.

    Returns (forum_group_id, topic_routes) where topic_routes maps
    thread_id -> {"cwd": "/path", "agent": "agent_name"}.
    """
    # Check user config first, fall back to system config
    config_path = Path.home() / ".aos" / "config" / "projects.yaml"
    if not config_path.exists():
        config_path = WORKSPACE / "config" / "projects.yaml"
    if not config_path.exists():
        logger.warning("projects.yaml not found — no topic routes loaded")
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

        # Resolve path (expand ~) — validate it exists
        raw_path = proj.get("path", f"~/{name}")
        resolved_path = str(Path(raw_path).expanduser())
        if not Path(resolved_path).is_dir():
            logger.warning(f"Route path missing for {name}: {resolved_path} — falling back to ~")
            bridge_event("route_path_missing", level="warn",
                         project=name, path=resolved_path)
            resolved_path = str(Path.home())

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
            "cwd": str(Path.home()),  # system agents work from home for full access
            "agent": agent_name,
        }

    logger.info(f"Loaded routes: group={forum_group_id}, topics={topic_routes}")
    return forum_group_id, topic_routes


def _parse_time(time_str: str, default_hour: int = 8, default_minute: int = 0) -> tuple[int, int]:
    """Parse a time string like '06:00', '9:00 PM', or '21:00' into (hour, minute)."""
    if not isinstance(time_str, str):
        return default_hour, default_minute
    try:
        if "PM" in time_str.upper():
            parts = time_str.upper().replace("PM", "").strip().split(":")
            hour = int(parts[0]) + (12 if int(parts[0]) != 12 else 0)
            minute = int(parts[1]) if len(parts) > 1 else 0
        elif "AM" in time_str.upper():
            parts = time_str.upper().replace("AM", "").strip().split(":")
            hour = int(parts[0]) % 12
            minute = int(parts[1]) if len(parts) > 1 else 0
        elif ":" in time_str:
            parts = time_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        else:
            return default_hour, default_minute
        return hour, minute
    except (ValueError, IndexError):
        return default_hour, default_minute


def _load_operator_config() -> dict:
    """Load operator.yaml — returns empty dict if missing (pre-onboarding)."""
    op_path = Path.home() / ".aos" / "config" / "operator.yaml"
    try:
        if op_path.exists():
            return yaml.safe_load(op_path.read_text()) or {}
    except Exception as e:
        logger.warning(f"Failed to read operator.yaml: {e}")
    return {}


def main():
    # ── PID lock — prevent duplicate instances ────────────────────────────────
    _acquire_pid_lock()

    # ── Wait for secrets (handles boot before onboarding completes) ───────────
    MAX_RETRIES = 5
    RETRY_DELAY = 30  # seconds

    bot_token = None
    chat_id_str = None

    for attempt in range(MAX_RETRIES):
        bot_token = _get_secret("TELEGRAM_BOT_TOKEN")
        chat_id_str = _get_secret("TELEGRAM_CHAT_ID")
        if bot_token and chat_id_str:
            break
        if attempt < MAX_RETRIES - 1:
            logger.warning(
                f"Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID "
                f"(attempt {attempt + 1}/{MAX_RETRIES}, retrying in {RETRY_DELAY}s)"
            )
            time.sleep(RETRY_DELAY)

    if not bot_token or not chat_id_str:
        logger.error(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in keychain after "
            f"{MAX_RETRIES} attempts. Is onboarding complete?"
        )
        sys.exit(1)

    chat_id = int(chat_id_str)

    # ── Load operator schedule ────────────────────────────────────────────────
    op_config = _load_operator_config()
    daily_loop = op_config.get("daily_loop", {})

    # ── Start heartbeat ──────────────────────────────────────────────────────
    from heartbeat import start_heartbeat
    start_heartbeat(bot_token, chat_id, interval_minutes=30)
    logger.info("Heartbeat started (30 min interval, active hours only)")

    # ── Morning briefing (from operator.yaml, default 08:00) ─────────────────
    from daily_briefing import start_daily_briefing
    briefing_time = daily_loop.get("morning_briefing", "08:00")
    br_hour, br_minute = _parse_time(str(briefing_time), default_hour=8, default_minute=0)
    start_daily_briefing(bot_token, chat_id, hour=br_hour, minute=br_minute)
    logger.info(f"Daily briefing scheduled at {br_hour:02d}:{br_minute:02d}")

    # ── Evening check-in (from operator.yaml, skipped if not configured) ─────
    evening_time = daily_loop.get("evening_checkin")
    if evening_time:
        ev_hour, ev_minute = _parse_time(str(evening_time), default_hour=21, default_minute=0)
        from evening_checkin import start_evening_checkin
        start_evening_checkin(bot_token, chat_id, hour=ev_hour, minute=ev_minute)
        logger.info(f"Evening check-in scheduled at {ev_hour:02d}:{ev_minute:02d}")

    # ── Temp file cleanup (hourly) ───────────────────────────────────────────
    _start_temp_cleanup(interval_minutes=60)

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
