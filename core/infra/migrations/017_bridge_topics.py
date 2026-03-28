"""
Migration 017: Bootstrap Bridge v2 forum topic infrastructure.

Creates ~/.aos/config/bridge-topics.yaml with:
1. forum_group_id from projects.yaml
2. Existing project topic IDs (nuchay, chief, etc.)
3. System topic slots (daily, alerts, work, knowledge, system)
4. Creates the 'daily' topic via Telegram API (always-on topic)

The TopicManager handles progressive creation of other topics at runtime.
"""

DESCRIPTION = "Bootstrap bridge forum topics (bridge-topics.yaml + daily topic)"

import json
import logging
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

HOME = Path.home()
PROJECTS_YAML = HOME / ".aos" / "config" / "projects.yaml"
CONFIG_PATH = HOME / ".aos" / "config" / "bridge-topics.yaml"

# Telegram API base
_TG_API = "https://api.telegram.org/bot{token}/{method}"


def _get_secret(key: str) -> str | None:
    """Retrieve a secret from macOS Keychain via agent-secret."""
    script = HOME / "aos" / "core" / "bin" / "agent-secret"
    try:
        result = subprocess.run(
            [str(script), "get", key],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Failed to get secret '{key}': {e}")
    return None


def _tg_request(token: str, method: str, payload: dict) -> dict | None:
    """Make a Telegram Bot API request. Returns 'result' or None."""
    url = _TG_API.format(token=token, method=method)
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
                print(f"       Telegram API {method} failed: {body}")
                return None
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            error_body = str(e)
        print(f"       Telegram API {method} HTTP {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"       Telegram API {method} error: {e}")
        return None


def _read_projects_yaml() -> dict | None:
    """Read and parse projects.yaml."""
    if not PROJECTS_YAML.exists():
        return None
    try:
        with open(PROJECTS_YAML) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"       Failed to read projects.yaml: {e}")
        return None


def _extract_forum_group_id(data: dict) -> int | None:
    """Extract forum_group_id from any project in projects.yaml."""
    projects = data.get("projects", {})
    for name, proj in projects.items():
        if isinstance(proj, dict):
            tg = proj.get("telegram", {})
            if isinstance(tg, dict) and tg.get("forum_group_id"):
                return tg["forum_group_id"]
    # Also check system-level
    system = data.get("system", {})
    if isinstance(system, dict):
        tg = system.get("telegram", {})
        if isinstance(tg, dict) and tg.get("forum_group_id"):
            return tg["forum_group_id"]
    return None


def _extract_project_topics(data: dict) -> dict:
    """Extract existing project topic IDs from projects.yaml."""
    result = {}
    projects = data.get("projects", {})
    for name, proj in projects.items():
        if isinstance(proj, dict):
            tg = proj.get("telegram", {})
            if isinstance(tg, dict):
                topic_id = tg.get("forum_topic_id")
                result[name] = {
                    "thread_id": topic_id,
                    "created": str(date.today()) if topic_id else None,
                    "pinned_message_id": None,
                }
    return result


def _atomic_write_yaml(path: Path, data: dict):
    """Write YAML atomically using os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        suffix=".tmp",
        prefix="bridge-topics-",
    )
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def check() -> bool:
    """Applied if bridge-topics.yaml exists and has a valid structure."""
    if not CONFIG_PATH.exists():
        return False
    try:
        with open(CONFIG_PATH) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False
        # Must have forum_group_id and topics section
        if not data.get("forum_group_id"):
            return False
        if not isinstance(data.get("topics"), dict):
            return False
        return True
    except Exception:
        return False


def up() -> bool:
    """Create bridge-topics.yaml and the daily topic."""

    # 1. Read projects.yaml for forum_group_id and existing topic IDs
    # Skip gracefully if config doesn't exist — forum topics are optional.
    # The TopicManager creates topics at runtime when config appears later.
    proj_data = _read_projects_yaml()
    if proj_data is None:
        print("       projects.yaml not found — skipping (forum topics are optional)")
        print("       TopicManager will handle this when projects.yaml is created")
        return True  # Don't block downstream migrations

    forum_group_id = _extract_forum_group_id(proj_data)
    if forum_group_id is None:
        print("       No forum_group_id in projects.yaml — skipping (forum topics are optional)")
        return True  # Don't block downstream migrations

    print(f"       Forum group ID: {forum_group_id}")

    # 2. Extract existing project topic IDs
    project_topics = _extract_project_topics(proj_data)
    for name, info in project_topics.items():
        tid = info.get("thread_id")
        status = f"thread_id={tid}" if tid else "pending"
        print(f"       Project '{name}': {status}")

    # 3. Build initial config
    config = {
        "forum_group_id": forum_group_id,
        "topics": {
            "daily": {"thread_id": None, "created": None, "pinned_message_id": None},
            "alerts": {"thread_id": None, "created": None, "pinned_message_id": None},
            "work": {"thread_id": None, "created": None, "pinned_message_id": None},
            "knowledge": {"thread_id": None, "created": None, "pinned_message_id": None},
            "system": {"thread_id": None, "created": None, "pinned_message_id": None},
        },
        "projects": project_topics,
    }

    # 4. Try to create the 'daily' topic via Telegram API
    bot_token = _get_secret("TELEGRAM_BOT_TOKEN")
    if bot_token:
        print("       Creating 'daily' forum topic...")
        result = _tg_request(bot_token, "createForumTopic", {
            "chat_id": forum_group_id,
            "name": "\U0001f305 Daily",
            "icon_color": 0x6FB9F0,
        })
        if result:
            thread_id = result.get("message_thread_id")
            print(f"       Created daily topic (thread_id={thread_id})")

            # Pin welcome message
            welcome = "Morning briefings and evening wraps appear here."
            msg_result = _tg_request(bot_token, "sendMessage", {
                "chat_id": forum_group_id,
                "message_thread_id": thread_id,
                "text": welcome,
            })
            pinned_id = None
            if msg_result:
                pinned_id = msg_result.get("message_id")
                _tg_request(bot_token, "pinChatMessage", {
                    "chat_id": forum_group_id,
                    "message_id": pinned_id,
                    "disable_notification": True,
                })
                print("       Pinned welcome message in daily topic")

            config["topics"]["daily"] = {
                "thread_id": thread_id,
                "created": str(date.today()),
                "pinned_message_id": pinned_id,
            }
        else:
            print("       Failed to create daily topic (will retry at runtime)")
    else:
        print("       Bot token not available — skipping daily topic creation")
        print("       TopicManager will create it on first bridge start")

    # 5. Write config
    _atomic_write_yaml(CONFIG_PATH, config)
    print(f"       Saved {CONFIG_PATH}")

    return True
