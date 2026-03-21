"""Render Claude stream events to Telegram messages."""

import logging
import random
import re
import time
from collections.abc import AsyncGenerator

from telegram import Bot
from telegram.error import BadRequest, TimedOut

from session_manager import (
    StreamEvent,
    TextDelta,
    TextComplete,
    ToolStart,
    ToolResult,
    SessionInit,
    SessionResult,
    ApiRetry,
    RateLimit,
)
from telegram_formatter import md_to_telegram_html

logger = logging.getLogger("bridge.renderer")

# Telegram limits
MAX_MESSAGE_LENGTH = 4096
EDIT_THROTTLE_SECONDS = 1.5  # min time between editMessageText calls
DRAFT_THROTTLE_SECONDS = 0.3  # min time between sendMessageDraft calls
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


async def _edit_safe(bot: Bot, chat_id: int, msg_id: int, text: str):
    """Edit a message with HTML fallback."""
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=msg_id, parse_mode="HTML"
        )
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        # HTML parse error — try plain
        plain = re.sub(r"<[^>]+>", "", text)
        if plain.strip():
            try:
                await bot.edit_message_text(
                    text=plain, chat_id=chat_id, message_id=msg_id
                )
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
) -> tuple[str, SessionResult | None]:
    """Render a stream of Claude events to Telegram.

    Returns (final_text, session_result).
    """
    accumulated_text = ""
    current_msg_id = None
    draft_id = random.randint(1, 2**31) if is_dm else None
    last_update_time = 0.0
    tool_status_msg_id = None
    session_result = None
    send_kwargs = {}
    if thread_id:
        send_kwargs["message_thread_id"] = thread_id

    async def _update_draft(text: str):
        """Send progressive draft update (DM only)."""
        nonlocal last_update_time
        now = time.monotonic()
        if now - last_update_time < DRAFT_THROTTLE_SECONDS:
            return
        try:
            html = _safe_html(text)
            if len(html) > MAX_MESSAGE_LENGTH:
                html = html[: MAX_MESSAGE_LENGTH - 20] + "\n<i>...</i>"
            await bot.send_message_draft(
                chat_id=chat_id,
                draft_id=draft_id,
                text=html,
                parse_mode="HTML",
            )
            last_update_time = now
        except Exception as e:
            logger.warning(f"Draft update failed: {e}")

    async def _update_edit(text: str):
        """Edit existing message (group/topic mode)."""
        nonlocal last_update_time
        now = time.monotonic()
        if now - last_update_time < EDIT_THROTTLE_SECONDS:
            return
        if current_msg_id:
            html = _safe_html(text)
            if len(html) > MAX_MESSAGE_LENGTH:
                html = html[: MAX_MESSAGE_LENGTH - 20] + "\n<i>...</i>"
            await _edit_safe(bot, chat_id, current_msg_id, html)
            last_update_time = now

    async def _show_tool_status(text: str):
        """Show/update tool use status line."""
        nonlocal tool_status_msg_id
        html = f"<i>{text}</i>"
        if tool_status_msg_id:
            try:
                await bot.edit_message_text(
                    text=html,
                    chat_id=chat_id,
                    message_id=tool_status_msg_id,
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            msg_id = await _send_safe(bot, chat_id, html, **send_kwargs)
            if msg_id:
                tool_status_msg_id = msg_id

    async def _cleanup_tool_status():
        """Delete the tool status message."""
        nonlocal tool_status_msg_id
        if tool_status_msg_id:
            try:
                await bot.delete_message(
                    chat_id=chat_id, message_id=tool_status_msg_id
                )
            except Exception:
                pass
            tool_status_msg_id = None

    # Process events
    async for event in events:
        if isinstance(event, TextDelta):
            # Clean up tool status when text starts flowing
            if tool_status_msg_id:
                await _cleanup_tool_status()

            accumulated_text += event.text

            if len(accumulated_text) < MIN_CHARS_TO_SHOW:
                continue

            if is_dm:
                await _update_draft(accumulated_text)
            else:
                if not current_msg_id:
                    html = _safe_html(accumulated_text)
                    current_msg_id = await _send_safe(
                        bot, chat_id, html, **send_kwargs
                    )
                    last_update_time = time.monotonic()
                else:
                    await _update_edit(accumulated_text)

        elif isinstance(event, ToolStart):
            await _show_tool_status(event.input_preview)

        elif isinstance(event, ToolResult):
            if event.is_error:
                await _show_tool_status(f"Tool error: {event.preview[:100]}")

        elif isinstance(event, TextComplete):
            # Full text from an assistant turn — use as authoritative
            accumulated_text = event.text

        elif isinstance(event, ApiRetry):
            await _show_tool_status(
                f"API retry ({event.attempt}/{event.max_retries}), "
                f"waiting {event.delay_ms}ms..."
            )

        elif isinstance(event, SessionResult):
            session_result = event
            if event.is_error and not accumulated_text:
                accumulated_text = event.text or "An error occurred."

    # Clean up tool status
    await _cleanup_tool_status()

    # Final delivery
    if not accumulated_text:
        accumulated_text = "(empty response)"

    html = _safe_html(accumulated_text)
    chunks = _split_message(html)

    if is_dm:
        # Send final message(s) to commit the draft
        for chunk in chunks:
            await _send_safe(bot, chat_id, chunk, **send_kwargs)
    else:
        # Final edit for the first chunk
        if current_msg_id and chunks:
            await _edit_safe(bot, chat_id, current_msg_id, chunks[0])
            # Send additional chunks as new messages
            for chunk in chunks[1:]:
                await _send_safe(bot, chat_id, chunk, **send_kwargs)
        elif chunks:
            # No message was sent yet (very short response)
            for chunk in chunks:
                await _send_safe(bot, chat_id, chunk, **send_kwargs)

    return accumulated_text, session_result
