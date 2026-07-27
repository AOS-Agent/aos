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
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

TOPICS_CONFIG = Path.home() / ".aos" / "config" / "bridge-topics.yaml"
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

VALID_TOPICS = ("daily", "alerts", "work", "knowledge", "system")

TELEGRAM_MSG_LIMIT = 4096
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


class _RateLimiter:
    """Minimum-interval limiter, thread-safe.

    Inlined rather than imported from ``lib.rate_limit``: aos-notify loads
    this module by file path, so router.py must stay stdlib-only.
    """

    def __init__(self, max_per_second: float = 1.0):
        self._interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()


# Telegram's recommended ceiling is 1 message/second per bot.
_RATE_LIMITER = _RateLimiter(max_per_second=1.0)

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


def get_routing() -> tuple[int | None, dict[str, int]]:
    """(forum_group_id, {topic: thread_id}) as the router currently sees it.

    Public read for health checks and setup tooling. Senders don't need this —
    they just call ``send_notification``.
    """
    return _load_topics()


def _split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split into chunks that fit Telegram's limit.

    Prefers a newline boundary, then a space, then a hard cut. Ported from
    ``lib.notify`` so migrated senders keep byte-identical chunking.
    """
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


def resolve_topic(topic: str | None, kind: str = "info") -> str:
    """Tier 1: sender-declared topic. Tier 2: infer from kind."""
    if topic in VALID_TOPICS:
        return topic
    if topic:
        log.warning("Unknown notify topic %r — falling back to inference", topic)
    return "alerts" if kind == "alert" else "system"


def _send_raw(token: str, chat_id: int | str, text: str,
              thread_id: int | None = None, parse_mode: str | None = "HTML",
              silent: bool = False, no_preview: bool = False) -> tuple[bool, str, int | None]:
    """One sendMessage call. Returns (ok, error_description, http_status)."""
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
    _RATE_LIMITER.wait()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        return bool(body.get("ok")), body.get("description", ""), resp.status
    except urllib.error.HTTPError as e:
        try:
            payload_err = json.loads(e.read().decode())
        except Exception:
            payload_err = {}
        desc = payload_err.get("description", str(e))
        if e.code == 429:
            retry_after = payload_err.get("parameters", {}).get("retry_after")
            if retry_after:
                desc = f"{desc}|retry_after={retry_after}"
        return False, desc, e.code
    except Exception as e:
        return False, str(e), None


def _send_with_retry(token: str, chat_id: int | str, text: str,
                     thread_id: int | None = None, parse_mode: str | None = "HTML",
                     silent: bool = False, no_preview: bool = False) -> tuple[bool, str]:
    """Send one chunk, retrying transient failures. Returns (ok, error).

    Retries 429s (honouring ``retry_after``) and network/5xx errors with
    exponential backoff. A 400 is permanent and returns immediately so the
    caller's tier chain can move on — except an HTML parse failure, which is
    retried once as plain text.
    """
    for attempt in range(MAX_RETRIES):
        ok, err, code = _send_raw(token, chat_id, text, thread_id,
                                  parse_mode, silent, no_preview)
        if ok:
            return True, ""

        if code == 429:
            delay = BACKOFF_BASE * (2 ** attempt)
            if "|retry_after=" in err:
                try:
                    delay = float(err.split("|retry_after=")[1])
                except (ValueError, IndexError):
                    pass
            log.warning("Telegram rate limited, retrying after %ss", delay)
            time.sleep(delay)
            continue

        if code == 400:
            # HTML that Telegram won't parse: strip tags and try once as plain.
            if parse_mode and "parse" in err.lower():
                log.warning("Telegram parse failed (%s) — retrying as plain text", err)
                ok, plain_err, _ = _send_raw(token, chat_id, re.sub(r"<[^>]+>", "", text),
                                             thread_id, None, silent, no_preview)
                return (True, "") if ok else (False, plain_err)
            # Any other 400 (thread gone, chat not found, ...) is permanent.
            return False, err

        if attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))

    return False, err


def send_notification(text: str, topic: str | None = None, kind: str = "info",
                      parse_mode: str | None = "HTML",
                      silent: bool = False, no_preview: bool = False) -> dict:
    """Deliver a notification, never silently dropping it.

    Messages over Telegram's 4096-char limit are split and delivered in
    order to a single destination. Transient failures (429, network, 5xx)
    are retried with backoff; unparseable HTML falls back to plain text.

    Returns a result dict: {"delivered": bool, "target": "topic:work" |
    "group" | "dm" | None, "error": str | None, "chunks": int,
    "partial": bool}. Callers may log it; they don't need to branch on it.
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

    # Build the tier chain: topic thread -> group General -> operator DM.
    tiers: list[tuple[int | str, int | None, str]] = []
    if group_id and thread_id:
        tiers.append((group_id, thread_id, f"topic:{resolved}"))
    if group_id:
        tiers.append((group_id, None, "group"))
    dm_chat_id = _get_secret("TELEGRAM_CHAT_ID")
    if dm_chat_id:
        tiers.append((dm_chat_id, None, "dm"))
    if not tiers:
        return {"delivered": False, "target": None, "error": "no delivery target configured"}

    chunks = _split_message(text)

    # Resolve the destination with the first chunk, then keep the rest of a
    # split message with it — a multi-part message must not scatter across
    # two different chats.
    chosen = None
    last_err = "no delivery target configured"
    for chat_id, thr, label in tiers:
        ok, err = _send_with_retry(token, chat_id, chunks[0], thr,
                                   parse_mode, silent, no_preview)
        if ok:
            chosen = (chat_id, thr, label)
            if label != tiers[0][2]:
                log.warning("Delivered to %s (preferred target unavailable)", label)
            break
        last_err = err
        if not any(m in err.lower() for m in _THREAD_GONE_MARKERS):
            log.warning("Send to %s failed (%s) — trying next target", label, err)

    if chosen is None:
        log.error("All notification targets failed: %s", last_err)
        return {"delivered": False, "target": None, "error": last_err}

    chat_id, thr, label = chosen
    partial_err = None
    for chunk in chunks[1:]:
        ok, err = _send_with_retry(token, chat_id, chunk, thr,
                                   parse_mode, silent, no_preview)
        if not ok:
            partial_err = err
            log.error("Chunk delivery to %s failed: %s", label, err)

    # The message reached the operator even if a trailing chunk did not, so
    # `delivered` stays True — a caller retrying would duplicate chunk 1.
    return {
        "delivered": True,
        "target": label,
        "error": partial_err,
        "chunks": len(chunks),
        "partial": partial_err is not None,
    }
