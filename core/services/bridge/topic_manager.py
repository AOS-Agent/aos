"""
Topic Manager — progressive Telegram forum topic management for Bridge v2.

Topics are created on-demand, not all at once. Each topic has a welcome
message that gets pinned when the topic is first created.

Config stored at ~/.aos/config/bridge-topics.yaml (user data, never in git).

Uses stdlib only (urllib.request) — no httpx dependency in this module.
"""

import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".aos" / "config" / "bridge-topics.yaml"

# Telegram API base
_TG_API = "https://api.telegram.org/bot{token}/{method}"

# Welcome messages pinned when a topic is first created
WELCOME_MESSAGES = {
    "daily": "Morning briefings and evening wraps appear here.",
    "alerts": "System alerts \u2014 service failures, disk warnings, security events.",
    "work": "Task updates \u2014 completions, reminders, overdue items.",
    "knowledge": "Knowledge captures \u2014 extractions, vault saves, research.",
    "system": "System health, agent activity, and session info. Pull-only.",
}

# Display names used when creating forum topics
TOPIC_DISPLAY_NAMES = {
    "daily": "\U0001f305 Daily",
    "alerts": "\U0001f6a8 Alerts",
    "work": "\u2705 Work",
    "knowledge": "\U0001f4da Knowledge",
    "system": "\u2699\ufe0f System",
}

# Icon colors for topics (Telegram forum topic icon colors, 6-digit hex as int)
# Telegram supports: 0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F
TOPIC_COLORS = {
    "daily": 0x6FB9F0,      # blue
    "alerts": 0xFB6F5F,     # red
    "work": 0x8EEE98,       # green
    "knowledge": 0xFFD67E,  # yellow
    "system": 0xCB86DB,     # purple
}


class TopicManager:
    """Manages Telegram forum topics progressively.

    Topics are created on first use. Config is persisted to
    bridge-topics.yaml with atomic writes.
    """

    def __init__(self, bot_token: str, forum_group_id: int):
        self.bot_token = bot_token
        self.forum_group_id = forum_group_id
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Read bridge-topics.yaml. Returns default structure if missing."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning(f"Failed to read {CONFIG_PATH}: {e}")

        # Default structure
        return {
            "forum_group_id": self.forum_group_id,
            "topics": {
                "daily": {"thread_id": None, "created": None, "pinned_message_id": None},
                "alerts": {"thread_id": None, "created": None, "pinned_message_id": None},
                "work": {"thread_id": None, "created": None, "pinned_message_id": None},
                "knowledge": {"thread_id": None, "created": None, "pinned_message_id": None},
                "system": {"thread_id": None, "created": None, "pinned_message_id": None},
            },
            "projects": {},
        }

    def _save_config(self):
        """Atomic write to bridge-topics.yaml using os.replace()."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=CONFIG_PATH.parent,
                suffix=".tmp",
                prefix="bridge-topics-",
            )
            with os.fdopen(fd, "w") as f:
                yaml.dump(self._config, f, default_flow_style=False, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            logger.error(f"Failed to save {CONFIG_PATH}: {e}")
            # Clean up temp file if it still exists
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def get_topic_thread_id(self, topic: str) -> int | None:
        """Return thread_id for a system topic, or None if not yet created."""
        topics = self._config.get("topics", {})
        entry = topics.get(topic, {})
        return entry.get("thread_id") if isinstance(entry, dict) else None

    def get_project_thread_id(self, project: str) -> int | None:
        """Return thread_id for a project topic, or None if not yet created."""
        projects = self._config.get("projects", {})
        entry = projects.get(project, {})
        return entry.get("thread_id") if isinstance(entry, dict) else None

    def ensure_topic(self, topic: str, name: str = None) -> int:
        """Create the topic if it doesn't exist, return its thread_id.

        For system topics (daily, alerts, work, knowledge, system),
        uses predefined display names and welcome messages.
        For custom/project topics, uses the provided name.

        Args:
            topic: Topic key (e.g. "daily", "alerts", or a project name).
            name: Display name for the topic. If None, uses TOPIC_DISPLAY_NAMES
                  for system topics, or the topic key capitalized for others.

        Returns:
            The thread_id (message_thread_id) for the topic.

        Raises:
            RuntimeError: If topic creation fails after the API call.
        """
        # Check if already exists
        existing = self.get_topic_thread_id(topic)
        if existing is not None:
            return existing

        # Also check projects
        existing = self.get_project_thread_id(topic)
        if existing is not None:
            return existing

        # Determine display name
        if name is None:
            name = TOPIC_DISPLAY_NAMES.get(topic, topic.capitalize())

        # Determine icon color
        icon_color = TOPIC_COLORS.get(topic, 0x6FB9F0)

        # Create the forum topic via Telegram API
        thread_id = self._api_create_forum_topic(name, icon_color)
        if thread_id is None:
            raise RuntimeError(f"Failed to create forum topic '{topic}'")

        # Pin a welcome message if this is a system topic
        pinned_message_id = None
        welcome = WELCOME_MESSAGES.get(topic)
        if welcome:
            pinned_message_id = self._api_send_and_pin(thread_id, welcome)

        # Determine where to store — system topic or project
        is_system_topic = topic in WELCOME_MESSAGES
        if is_system_topic:
            if "topics" not in self._config:
                self._config["topics"] = {}
            self._config["topics"][topic] = {
                "thread_id": thread_id,
                "created": str(date.today()),
                "pinned_message_id": pinned_message_id,
            }
        else:
            if "projects" not in self._config:
                self._config["projects"] = {}
            self._config["projects"][topic] = {
                "thread_id": thread_id,
                "created": str(date.today()),
                "pinned_message_id": pinned_message_id,
            }

        self._save_config()
        logger.info(f"Created forum topic '{topic}' (thread_id={thread_id})")
        return thread_id

    # ── Telegram API helpers (stdlib only) ────────────────────────────────

    def _tg_request(self, method: str, payload: dict) -> dict | None:
        """Make a Telegram Bot API request using urllib.

        Returns the parsed 'result' on success, None on failure.
        Never raises — all errors are logged and swallowed.
        """
        url = _TG_API.format(token=self.bot_token, method=method)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok"):
                    return body.get("result")
                else:
                    logger.error(f"Telegram API {method} failed: {body}")
                    return None
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = str(e)
            logger.error(f"Telegram API {method} HTTP {e.code}: {error_body}")
            return None
        except Exception as e:
            logger.error(f"Telegram API {method} error: {e}")
            return None

    def _api_create_forum_topic(self, name: str, icon_color: int = 0x6FB9F0) -> int | None:
        """Create a forum topic. Returns the message_thread_id or None."""
        result = self._tg_request("createForumTopic", {
            "chat_id": self.forum_group_id,
            "name": name,
            "icon_color": icon_color,
        })
        if result:
            thread_id = result.get("message_thread_id")
            logger.info(f"Created forum topic '{name}' -> thread_id={thread_id}")
            return thread_id
        return None

    def _api_send_and_pin(self, thread_id: int, text: str) -> int | None:
        """Send a message to a topic and pin it. Returns message_id or None."""
        # Send the message
        result = self._tg_request("sendMessage", {
            "chat_id": self.forum_group_id,
            "message_thread_id": thread_id,
            "text": text,
        })
        if not result:
            return None

        message_id = result.get("message_id")

        # Pin it (silently)
        self._tg_request("pinChatMessage", {
            "chat_id": self.forum_group_id,
            "message_id": message_id,
            "disable_notification": True,
        })

        return message_id
