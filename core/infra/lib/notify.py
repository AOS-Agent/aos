"""Shared Telegram notification helper for AOS.

Single source of truth for sending Telegram messages across the system.
Handles credential lookup, message splitting, and retry with backoff.

Usage:
    from lib.notify import send_telegram
    send_telegram("Hello from AOS")
    send_telegram("<b>HTML</b> message", parse_mode="HTML")
    send_telegram("Into a topic", thread_id=12345)
"""

import logging
import os
import subprocess
import time
import urllib.request
import urllib.error
import json

from lib.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

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


def send_telegram(
    text: str,
    parse_mode: str = "HTML",
    thread_id: int | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Send a Telegram message with automatic splitting and retry.

    Args:
        text: Message text to send
        parse_mode: "HTML" or "Markdown" (default: HTML)
        thread_id: Forum topic thread ID (optional)
        bot_token: Override bot token (default: reads from keychain)
        chat_id: Override chat ID (default: reads from keychain)

    Returns:
        True if all chunks sent successfully, False otherwise.
    """
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
