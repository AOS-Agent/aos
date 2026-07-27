"""Shared Telegram notification helper for AOS.

Thin compatibility layer over ``engine.notify.router`` — the single source
of truth for outbound delivery. Calls route to the operator's forum topic,
falling back to group General and then the operator DM, so installs with no
forum configured behave exactly as before.

New code should call ``engine.notify.router.send_notification`` directly.
This wrapper exists so existing ``send_telegram`` callers keep working.

Usage:
    from lib.notify import send_telegram
    send_telegram("Hello from AOS")
    send_telegram("Deploy finished", topic="system")
    send_telegram("Disk almost full", kind="alert")
    send_telegram("To a specific chat", chat_id="123")  # bypasses routing
"""

import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from lib.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

# Lazily-loaded notify router module (see _load_router).
_ROUTER = None

# Enforce Telegram's recommended max of 1 message/second per bot.
_RATE_LIMITER = RateLimiter(max_per_second=1.0)

TELEGRAM_MSG_LIMIT = 4096
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


def _get_secret(key: str) -> str | None:
    """Read a secret from the AOS keychain helper."""
    script = os.path.join(os.path.expanduser("~"), "aos", "core", "bin", "agent-secret")
    try:
        result = subprocess.run(
            [script, "get", key],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split a message into chunks that fit within Telegram's limit.

    Tries to split at newlines first, then at spaces, then hard-cuts.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to split at a newline
        cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            # Try to split at a space
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut < limit // 2:
            # Hard cut
            cut = limit

        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    return chunks


def _load_router():
    """Load the notify router (cached), preferring a real package import.

    A package import matters: it puts the module in sys.modules, so every
    sender in the process shares ONE router object — and therefore one rate
    limiter. Loading by file path creates a private copy, which would let
    two senders each send at the 1 msg/sec cap and blow through Telegram's
    limit. The path fallback stays for callers whose sys.path cannot reach
    core/engine.
    """
    global _ROUTER
    if _ROUTER is not None:
        return _ROUTER

    for engine in (Path(__file__).resolve().parents[2] / "engine",
                   Path.home() / "aos" / "core" / "engine"):
        router_py = engine / "notify" / "router.py"
        if not router_py.exists():
            continue

        if str(engine) not in sys.path:
            sys.path.insert(0, str(engine))
        try:
            _ROUTER = importlib.import_module("notify.router")
            return _ROUTER
        except Exception as e:
            logger.debug("notify.router package import failed (%s) — using file path", e)

        try:
            spec = importlib.util.spec_from_file_location("notify.router", router_py)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Publish it so a later `import notify.router` reuses this object
            # instead of building a second one.
            sys.modules.setdefault("notify.router", module)
            _ROUTER = module
            return _ROUTER
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not load notify router from %s: %s", router_py, e)
    return None


def send_telegram(
    text: str,
    parse_mode: str = "HTML",
    thread_id: int | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
    topic: str | None = None,
    kind: str = "info",
) -> bool:
    """Send a Telegram message, topic-routed by default.

    Routes through the notify router so messages land in the operator's
    forum topic. The router's own fallback chain (topic -> group General ->
    operator DM) covers installs with no forum configured, so this is safe
    for DM-only setups.

    Args:
        text: Message text to send
        parse_mode: "HTML" or "Markdown" (default: HTML)
        thread_id: Explicit forum thread — bypasses routing (legacy override)
        bot_token: Override bot token — bypasses routing (legacy override)
        chat_id: Override chat ID — bypasses routing (legacy override)
        topic: Router topic (daily/alerts/work/knowledge/system)
        kind: "info", "alert", or "success" — infers topic when none given

    Returns:
        True if the message was delivered, False otherwise.
    """
    # Explicit destination overrides keep the old direct-send path: a caller
    # that names a chat means it.
    if not (bot_token or chat_id or thread_id):
        router = _load_router()
        if router is not None:
            result = router.send_notification(
                text, topic=topic, kind=kind, parse_mode=parse_mode,
            )
            if result.get("error") and not result.get("delivered"):
                logger.warning("Telegram delivery failed: %s", result["error"])
            return bool(result.get("delivered"))
        logger.warning("Notify router unavailable — falling back to direct send")

    if not bot_token:
        bot_token = _get_secret("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        chat_id = _get_secret("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not available — skipping notification")
        return False

    chunks = _split_message(text)
    all_ok = True

    for chunk in chunks:
        if not chunk.strip():
            continue

        payload = {
            "chat_id": chat_id,
            "text": chunk,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if thread_id:
            payload["message_thread_id"] = thread_id

        success = _send_with_retry(bot_token, payload)
        if not success:
            all_ok = False

    return all_ok


def _send_with_retry(bot_token: str, payload: dict) -> bool:
    """Send a single Telegram API request with exponential backoff."""
    _RATE_LIMITER.wait()
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status == 200:
                return True
            logger.warning(f"Telegram API returned {resp.status} on attempt {attempt + 1}")
        except urllib.error.HTTPError as e:
            # 429 = rate limited, retry with backoff
            if e.code == 429:
                retry_after = BACKOFF_BASE * (2 ** attempt)
                try:
                    body = json.loads(e.read())
                    retry_after = body.get("parameters", {}).get("retry_after", retry_after)
                except Exception:
                    pass
                logger.warning(f"Telegram rate limited, retrying after {retry_after}s")
                time.sleep(retry_after)
                continue
            # 400 = bad request (usually HTML parse error), try without parse_mode
            if e.code == 400 and payload.get("parse_mode"):
                logger.warning("Telegram HTML parse failed, retrying as plain text")
                fallback = dict(payload)
                fallback.pop("parse_mode", None)
                return _send_with_retry_plain(bot_token, fallback)
            logger.error(f"Telegram API error {e.code}: {e.reason}")
            return False
        except Exception as e:
            logger.error(f"Telegram send failed (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))

    return False


def _send_with_retry_plain(bot_token: str, payload: dict) -> bool:
    """Send a plain-text fallback (single attempt, no parse_mode)."""
    import re
    payload["text"] = re.sub(r"<[^>]+>", "", payload["text"])
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram plain-text fallback failed: {e}")
        return False
