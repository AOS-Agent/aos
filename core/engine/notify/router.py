"""Telegram notification router — topic-aware outbound delivery.

Every proactive sender (crons, bus consumers, scheduler, council) routes
through here instead of hitting the Bot API with a bare TELEGRAM_CHAT_ID.

Routing tiers (deterministic — no content analysis):

    1. Sender declares a category via ``topic=`` — the sender knows what
       it is (watchdog -> "system", work reminders -> "work", ...).
    2. Kind infers when no topic given: ``kind="alert"`` -> alerts topic,
       everything else -> system topic.
    3. Fallback chain on delivery: forum topic -> group General -> operator
       DM. A disabled-Topics toggle or deleted topic never drops a message.

Config: ``~/.aos/config/bridge-topics.yaml`` (written by the bridge's
TopicManager). Credentials: macOS Keychain via agent-secret. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

TOPICS_CONFIG = Path.home() / ".aos" / "config" / "bridge-topics.yaml"
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

VALID_TOPICS = ("daily", "alerts", "work", "knowledge", "system")

# Telegram 400 descriptions that mean "the thread/forum is gone" — retry
# the same chat without a thread id rather than failing.
_THREAD_GONE_MARKERS = (
    "message thread not found",
    "topic_deleted",
    "topic_closed",
    "not a forum",
    "forum",
)

_KIND_PREFIX = {"alert": "⚠️", "success": "✅", "info": "ℹ️"}


def _get_secret(name: str) -> str | None:
    try:
        result = subprocess.run(
            [str(AGENT_SECRET), "get", name],
            capture_output=True, text=True, timeout=5,
        )
        value = result.stdout.strip()
        return value if value and result.returncode == 0 else None
    except Exception:
        return None


def _load_topics() -> tuple[int | None, dict[str, int]]:
    """Return (forum_group_id, {topic: thread_id}) from bridge-topics.yaml.

    Uses a tolerant line parser so bash-invoked paths need no PyYAML.
    """
    if not TOPICS_CONFIG.exists():
        return None, {}
    try:
        import yaml  # available in the service venvs
        data = yaml.safe_load(TOPICS_CONFIG.read_text()) or {}
    except ImportError:
        data = _parse_topics_minimal(TOPICS_CONFIG.read_text())
    except Exception as e:
        log.warning("Failed to read %s: %s", TOPICS_CONFIG, e)
        return None, {}

    gid = data.get("forum_group_id")
    topics = {}
    for name, entry in (data.get("topics") or {}).items():
        if isinstance(entry, dict) and entry.get("thread_id"):
            topics[name] = int(entry["thread_id"])
    return (int(gid) if gid else None), topics


def _parse_topics_minimal(text: str) -> dict:
    """PyYAML-free fallback parser for bridge-topics.yaml's flat shape."""
    data: dict = {"topics": {}}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("forum_group_id:"):
            try:
                data["forum_group_id"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("  ") and stripped.endswith(":") and not line.startswith("    "):
            current = stripped[:-1]
            data["topics"][current] = {}
        elif current and stripped.startswith("thread_id:"):
            try:
                data["topics"][current]["thread_id"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
    return data


def resolve_topic(topic: str | None, kind: str = "info") -> str:
    """Tier 1: sender-declared topic. Tier 2: infer from kind."""
    if topic in VALID_TOPICS:
        return topic
    if topic:
        log.warning("Unknown notify topic %r — falling back to inference", topic)
    return "alerts" if kind == "alert" else "system"


def _send_raw(token: str, chat_id: int | str, text: str,
              thread_id: int | None = None, parse_mode: str | None = "HTML",
              silent: bool = False, no_preview: bool = False) -> tuple[bool, str]:
    """One sendMessage call. Returns (ok, error_description)."""
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_notification": silent,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if thread_id:
        payload["message_thread_id"] = thread_id
    if no_preview:
        payload["disable_web_page_preview"] = True

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        return bool(body.get("ok")), body.get("description", "")
    except urllib.error.HTTPError as e:
        try:
            desc = json.loads(e.read().decode()).get("description", str(e))
        except Exception:
            desc = str(e)
        return False, desc
    except Exception as e:
        return False, str(e)


def send_notification(text: str, topic: str | None = None, kind: str = "info",
                      parse_mode: str | None = "HTML",
                      silent: bool = False, no_preview: bool = False) -> dict:
    """Deliver a notification, never silently dropping it.

    Returns a result dict: {"delivered": bool, "target": "topic:work" |
    "group" | "dm" | None, "error": str | None}. Callers may log it;
    they don't need to branch on it.
    """
    if not text:
        return {"delivered": False, "target": None, "error": "empty text"}

    token = _get_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        log.info("Notification (no Telegram configured): %s", text[:100])
        return {"delivered": False, "target": None, "error": "no bot token"}

    prefix = _KIND_PREFIX.get(kind)
    if prefix and not text.startswith(prefix):
        text = f"{prefix} {text}"

    group_id, topics = _load_topics()
    resolved = resolve_topic(topic, kind)
    thread_id = topics.get(resolved)

    # Tier: group + topic thread
    if group_id and thread_id:
        ok, err = _send_raw(token, group_id, text, thread_id, parse_mode, silent, no_preview)
        if ok:
            return {"delivered": True, "target": f"topic:{resolved}", "error": None}
        lowered = err.lower()
        if not any(m in lowered for m in _THREAD_GONE_MARKERS):
            log.warning("Topic send failed (%s) — trying General", err)

    # Tier: group General (topics disabled/deleted, or no thread known)
    if group_id:
        ok, err = _send_raw(token, group_id, text, None, parse_mode, silent, no_preview)
        if ok:
            if thread_id:
                log.warning("Topic %r unavailable — delivered to group General", resolved)
            return {"delivered": True, "target": "group", "error": None}
        log.warning("Group send failed (%s) — falling back to DM", err)

    # Tier: operator DM
    chat_id = _get_secret("TELEGRAM_CHAT_ID")
    if chat_id:
        ok, err = _send_raw(token, chat_id, text, None, parse_mode, silent, no_preview)
        if ok:
            return {"delivered": True, "target": "dm", "error": None}
        log.error("All notification targets failed: %s", err)
        return {"delivered": False, "target": None, "error": err}

    return {"delivered": False, "target": None, "error": "no delivery target configured"}
