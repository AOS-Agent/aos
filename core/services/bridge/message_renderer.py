"""Render Claude stream events to Telegram messages."""

import logging
import re
import time
from collections.abc import AsyncGenerator

from bridge_events import bridge_event
from longform_handler import is_longform, publish_longform
from session_manager import (
    ApiRetry,
    RateLimit,
    SessionInit,
    SessionResult,
    StreamEvent,
    TextComplete,
    TextDelta,
    ToolResult,
    ToolStart,
)
from telegram import Bot
from telegram.error import BadRequest, TimedOut
from telegram_formatter import md_to_telegram_html

logger = logging.getLogger("bridge.renderer")

# Telegram limits
MAX_MESSAGE_LENGTH = 4096
EDIT_THROTTLE_SECONDS = 1.5  # min time between editMessageText calls

MIN_CHARS_TO_SHOW = 30  # wait for this much text before first send


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text at natural boundaries (paragraphs, headings, code blocks)."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_len:
        split = max_len
        # Try splitting at paragraph break
        para = remaining.rfind("\n\n", 0, max_len)
        if para > max_len * 0.3:
            split = para + 2
        else:
            # Try splitting at any newline
            nl = remaining.rfind("\n", 0, max_len)
            if nl > max_len * 0.3:
                split = nl + 1

        chunks.append(remaining[:split])
        remaining = remaining[split:]

    if remaining:
        chunks.append(remaining)

    return chunks


def _safe_html(text: str) -> str:
    """Convert markdown to Telegram HTML, with fallback to plain text."""
    try:
        return md_to_telegram_html(text) or text
    except Exception:
        return text


async def _send_safe(bot: Bot, chat_id: int, text: str, **kwargs) -> int | None:
    """Send a message with HTML fallback. Returns message_id or None."""
    try:
        msg = await bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML", **kwargs
        )
        return msg.message_id
    except BadRequest:
        # HTML parse error — strip tags and retry
        plain = re.sub(r"<[^>]+>", "", text)
        if plain.strip():
            try:
                msg = await bot.send_message(
                    chat_id=chat_id, text=plain, **kwargs
                )
                return msg.message_id
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
    return None


async def _edit_safe(bot: Bot, chat_id: int, msg_id: int, text: str,
                     reply_markup=None):
    """Edit a message with HTML fallback. Pass reply_markup to keep/change buttons."""
    kwargs = {"text": text, "chat_id": chat_id, "message_id": msg_id, "parse_mode": "HTML"}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await bot.edit_message_text(**kwargs)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        # HTML parse error — try plain
        plain = re.sub(r"<[^>]+>", "", text)
        if plain.strip():
            kwargs["text"] = plain
            kwargs.pop("parse_mode", None)
            try:
                await bot.edit_message_text(**kwargs)
            except Exception:
                pass
    except TimedOut:
        pass
    except Exception as e:
        logger.debug(f"Edit failed: {e}")


async def render_stream(
    bot: Bot,
    chat_id: int,
    events: AsyncGenerator[StreamEvent, None],
    thread_id: int | None = None,
    is_dm: bool = False,
    is_resumed: bool = False,
    initial_msg_id: int | None = None,
    user_query: str | None = None,
) -> tuple[str, SessionResult | None]:
    """Render a stream of Claude events to Telegram.

    Single-message flow: the initial_msg_id ("Thinking..." + Stop button) gets
    progressively edited — tool status → streaming text → final response.
    One message throughout, no ghost notifications.

    Long responses (>6000 chars) are saved to vault + published to Telegraph,
    with a summary + link sent inline instead of chunked messages.

    Returns (final_text, session_result).
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    accumulated_text = ""
    msg_id = initial_msg_id  # THE message — everything edits this
    last_update_time = 0.0
    session_result = None
    send_kwargs = {}
    if thread_id:
        send_kwargs["message_thread_id"] = thread_id

    # Stop button — kept during thinking/tools, removed when final response delivered
    _stop_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("■ Stop", callback_data="stop_generation")
    ]])

    async def _edit_msg(html: str, keep_stop: bool = False):
        """Edit the single message. Throttled to avoid rate limits."""
        nonlocal last_update_time
        now = time.monotonic()
        if now - last_update_time < EDIT_THROTTLE_SECONDS:
            return
        if msg_id:
            if len(html) > MAX_MESSAGE_LENGTH:
                html = html[: MAX_MESSAGE_LENGTH - 20] + "\n<i>...</i>"
            markup = _stop_markup if keep_stop else None
            await _edit_safe(bot, chat_id, msg_id, html, reply_markup=markup)
            last_update_time = now

    async def _ensure_msg(html: str, keep_stop: bool = False):
        """Make sure we have a message. Create if needed, edit if exists."""
        nonlocal msg_id, last_update_time
        if msg_id:
            await _edit_msg(html, keep_stop=keep_stop)
        else:
            msg_id = await _send_safe(bot, chat_id, html, **send_kwargs)
            last_update_time = time.monotonic()

    # Process events — single message, progressively edited
    async for event in events:
        if isinstance(event, SessionInit):
            continue

        elif isinstance(event, TextDelta):
            accumulated_text += event.text
            if len(accumulated_text) < MIN_CHARS_TO_SHOW:
                continue
            html = _safe_html(accumulated_text)
            await _ensure_msg(html, keep_stop=True)

        elif isinstance(event, ToolStart):
            # Edit the thinking message with what Claude is actually doing
            if not accumulated_text:
                await _ensure_msg(f"<i>{event.input_preview}</i>", keep_stop=True)

        elif isinstance(event, ToolResult):
            if event.is_error and not accumulated_text:
                await _ensure_msg(f"<i>⚠️ {event.preview[:200]}</i>", keep_stop=True)

        elif isinstance(event, TextComplete):
            accumulated_text = event.text

        elif isinstance(event, RateLimit):
            wait_s = max(0, event.resets_at - int(time.time()))
            if wait_s > 0:
                wait_min = (wait_s + 59) // 60
                await _ensure_msg(
                    f"<i>⏳ Rate limited (~{wait_min} min). "
                    f"Quick commands still work — try \"tasks\" or \"add task: X\"</i>"
                )
            bridge_event("rate_limit", level="warn",
                         status=event.status, resets_at=event.resets_at, wait_s=wait_s)

        elif isinstance(event, ApiRetry):
            if not accumulated_text:
                await _ensure_msg(
                    f"<i>Retrying ({event.attempt}/{event.max_retries})...</i>"
                )
            bridge_event("api_retry", level="warn",
                         attempt=event.attempt, delay_ms=event.delay_ms)

        elif isinstance(event, SessionResult):
            session_result = event
            if event.is_error and not accumulated_text:
                accumulated_text = event.text or "An error occurred."

    # Final delivery — remove stop button, deliver complete response
    if not accumulated_text:
        accumulated_text = "(empty response)"

    _no_markup = InlineKeyboardMarkup([])

    # Long response → save to vault + Telegraph, send summary + link
    if is_longform(accumulated_text):
        try:
            metadata = {"user_query": user_query}
            if session_result and session_result.session_id:
                metadata["session_id"] = session_result.session_id

            result_info = publish_longform(
                markdown=accumulated_text,
                metadata=metadata,
            )

            # Build summary message with link
            summary_html = _safe_html(result_info["summary"])
            parts = [summary_html]

            if result_info["telegraph_url"]:
                parts.append(
                    f'\n\n📖 <a href="{result_info["telegraph_url"]}">Read full response</a>'
                )

            vault_rel = str(result_info["vault_path"]).replace(
                str(result_info["vault_path"].home()) + "/", "~/")
            parts.append(f'\n💾 <i>Saved to {vault_rel}</i>')

            final_html = "".join(parts)

            if msg_id:
                await _edit_safe(bot, chat_id, msg_id, final_html,
                                 reply_markup=_no_markup)
            else:
                await _send_safe(bot, chat_id, final_html, **send_kwargs)

            bridge_event("longform_published",
                         chars=len(accumulated_text),
                         telegraph=bool(result_info["telegraph_url"]),
                         vault=str(result_info["vault_path"]))
            logger.info(f"Longform response ({len(accumulated_text)} chars) → "
                        f"vault + telegraph")

        except Exception as e:
            # Fallback: if longform publish fails, deliver normally
            logger.error(f"Longform publish failed, falling back to chunks: {e}")
            html = _safe_html(accumulated_text)
            chunks = _split_message(html)
            if msg_id and chunks:
                await _edit_safe(bot, chat_id, msg_id, chunks[0],
                                 reply_markup=_no_markup)
                for chunk in chunks[1:]:
                    await _send_safe(bot, chat_id, chunk, **send_kwargs)
            elif chunks:
                for chunk in chunks:
                    await _send_safe(bot, chat_id, chunk, **send_kwargs)
    else:
        # Normal short response — inline delivery
        html = _safe_html(accumulated_text)
        chunks = _split_message(html)

        if msg_id and chunks:
            await _edit_safe(bot, chat_id, msg_id, chunks[0],
                             reply_markup=_no_markup)
            for chunk in chunks[1:]:
                await _send_safe(bot, chat_id, chunk, **send_kwargs)
        elif chunks:
            for chunk in chunks:
                await _send_safe(bot, chat_id, chunk, **send_kwargs)

    return accumulated_text, session_result
