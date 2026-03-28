"""Telegram channel — polls for messages, routes to Claude, sends responses."""

import asyncio
import fcntl
import json
import logging
import os
import re
import subprocess
import tempfile
import time as _time
from pathlib import Path

from telegram import Update, ReactionTypeEmoji
from telegram.error import BadRequest, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from session_manager import stream_claude, clear_session, cancel_stream, SessionResult, get_session_id
from message_renderer import render_stream
from interactive_buttons import (
    detect_options,
    build_option_keyboard,
    build_quick_action_keyboard,
    resolve_callback,
)
from activity_client import log_activity, log_conversation, update_conversation
from telegram_formatter import md_to_telegram_html
from voice_transcriber import transcribe_voice, set_mode, get_mode
from evening_checkin import (
    is_awaiting_checkin_reply, mark_checkin_replied,
    was_checkin_replied, _save_checkin_to_daily,
)
from execution_logger import log_execution
from bridge_events import bridge_event
from intent_classifier import dispatch as classify_intent

logger = logging.getLogger(__name__)

# ── Task dispatch ──────────────────────────────────────
# Tasks that should run in tmux (non-blocking) instead of streaming.
# The agent in the tmux session decides which tools to use (STEER, obsidian CLI,
# Drive, AppleScript, etc.) — the bridge doesn't guess.
_TASK_KEYWORDS = [
    # Explicit triggers
    "/do ", "/task ", "/steer ",
    # Desktop/GUI automation (agent will pick STEER vs CLI)
    "open ", "launch ", "use steer", "use computer",
    "click ", "navigate to ",
    "on the desktop", "on my screen", "screenshot",
    "graph view", "open the app", "switch to ",
    # Multi-step work (agent needs time + tools)
    "find and ", "search and ", "organize ", "create a note",
    "go through ", "check all ", "summarize the ",
    "set up ", "configure ", "install ",
]

def _is_task_dispatch(text: str) -> bool:
    """Detect if a message is a TASK (needs tmux dispatch) vs CHAT (stream response).

    Tasks: multi-step work, GUI automation, file operations, etc.
    Chat: questions, quick answers, code help, conversation.

    The agent in the tmux session picks the right tools — STEER for GUI,
    obsidian CLI for vault ops, Drive for terminal, APIs for services.
    """
    lower = text.lower().strip()
    # Explicit triggers
    if lower.startswith(("/do ", "/task ", "/steer ", "steer:")):
        return True
    # Keyword matching
    return any(kw in lower for kw in _TASK_KEYWORDS)

async def _dispatch_steer_and_report(chat, message, text: str, thread_id=None):
    """Dispatch a STEER job via tmux and report results back to Telegram."""
    try:
        dispatch_script = Path.home() / "aos" / "core" / "steer" / "dispatch.py"
        # Create and dispatch the job
        result = subprocess.run(
            ["python3", str(dispatch_script), "run", text],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            await message.reply_text(
                f"❌ Failed to dispatch STEER job: {result.stderr[:200]}",
                message_thread_id=thread_id,
            )
            return ""

        job_data = json.loads(result.stdout)
        job_id = job_data["job_id"]

        # Acknowledge dispatch
        send_kwargs = {}
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id
        status_msg = await chat.send_message(
            f"🔄 Working on it...\n<code>job: {job_id}</code>",
            parse_mode="HTML", **send_kwargs,
        )

        # Poll for completion (async, non-blocking)
        poll_script = str(dispatch_script)
        max_wait = 300  # 5 minutes
        poll_interval = 5
        elapsed = 0

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            poll_result = subprocess.run(
                ["python3", poll_script, "poll", job_id],
                capture_output=True, text=True, timeout=10,
            )
            if poll_result.returncode != 0:
                continue

            job_status = json.loads(poll_result.stdout)
            status = job_status.get("status", "unknown")

            if status == "completed":
                summary = job_status.get("summary", "Task completed (no summary).")
                apps = job_status.get("apps_opened", [])
                updates = job_status.get("updates", [])

                response_text = f"✅ <b>Done</b>\n\n{summary}"
                if apps:
                    response_text += f"\n\n<i>Apps used: {', '.join(apps)}</i>"

                try:
                    await status_msg.edit_text(response_text, parse_mode="HTML")
                except Exception:
                    await chat.send_message(response_text, parse_mode="HTML", **send_kwargs)

                # Cleanup
                subprocess.run(
                    ["python3", poll_script, "cleanup", job_id],
                    capture_output=True, text=True,
                )
                log_activity("telegram", "steer_job_completed", summary=summary[:100])
                return summary

            elif status == "failed":
                error = job_status.get("error", "Unknown error")
                try:
                    await status_msg.edit_text(f"❌ <b>Failed</b>\n\n{error}", parse_mode="HTML")
                except Exception:
                    await chat.send_message(f"❌ Failed: {error}", **send_kwargs)

                subprocess.run(
                    ["python3", poll_script, "cleanup", job_id],
                    capture_output=True, text=True,
                )
                log_activity("telegram", "steer_job_failed", summary=error[:100])
                return f"Failed: {error}"

            # Still running — update progress if there are new updates
            updates = job_status.get("updates", [])
            if updates and elapsed % 15 == 0:  # Update every 15s
                latest = updates[-1] if updates else ""
                try:
                    await status_msg.edit_text(
                        f"🔄 Working...\n<code>{latest[:100]}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        # Timeout
        try:
            await status_msg.edit_text(
                f"⏰ Job timed out after {max_wait}s.\n<code>job: {job_id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        subprocess.run(["python3", poll_script, "cleanup", job_id], capture_output=True, text=True)
        return "Job timed out"

    except Exception as e:
        logger.error(f"STEER dispatch error: {e}")
        await message.reply_text(
            f"❌ STEER dispatch error: {str(e)[:200]}",
            message_thread_id=thread_id,
        )
        return f"Error: {e}"

# In-flight message persistence — survives bridge restarts
# Stored in runtime dir (~/.aos/), NOT in the git-tracked code tree
_RUNTIME_DIR = Path.home() / ".aos" / "services" / "bridge"
_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
_INFLIGHT_PATH = _RUNTIME_DIR / ".inflight.json"


def _save_inflight(chat_id: int, text: str, user_key: str,
                   thread_id: int = None, cwd: str = None):
    """Persist an in-flight message atomically so it can be replayed after restart."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "user_key": user_key,
        "thread_id": thread_id,
        "cwd": cwd,
        "timestamp": _time.time(),
    }
    try:
        fd, tmp_path = tempfile.mkstemp(dir=_RUNTIME_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
            fcntl.flock(f, fcntl.LOCK_UN)
        os.rename(tmp_path, _INFLIGHT_PATH)
        bridge_event("inflight_saved", user_key=user_key)
    except Exception as e:
        logger.warning(f"Failed to save in-flight message: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _clear_inflight():
    """Remove the in-flight marker after successful response."""
    try:
        _INFLIGHT_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _load_inflight() -> dict | None:
    """Load a saved in-flight message if one exists and is recent (<5 min)."""
    try:
        if not _INFLIGHT_PATH.exists():
            return None
        with open(_INFLIGHT_PATH, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        age = _time.time() - data.get("timestamp", 0)
        if age > 300:  # older than 5 minutes — stale, discard
            _INFLIGHT_PATH.unlink(missing_ok=True)
            bridge_event("inflight_stale", level="warn", age_s=int(age))
            return None
        return data
    except Exception as e:
        logger.warning(f"Failed to load in-flight message: {e}")
        return None

TELEGRAM_MSG_LIMIT = 4096
TYPING_INTERVAL = 4.0  # seconds between typing keepalive pings
EDIT_INTERVAL = 2.0  # seconds between editing the live message
MIN_CHARS_TO_SHOW = 30  # wait for this much text before first message
MIN_CHUNK_BEFORE_SPLIT = 300  # minimum chars in a chunk before splitting

# Patterns that indicate a natural split point in raw markdown
_SPLIT_PATTERN = re.compile(
    r'\n(?=#{1,3} )'  # markdown heading
    r'|\n\n(?=\S)',    # paragraph break followed by content
    re.MULTILINE,
)


WORKSPACE = Path.home() / "aos"

# Telegram-supported HTML tags
_TG_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
            "code", "pre", "a", "blockquote", "tg-spoiler", "tg-emoji"}


def _sanitize_html(text: str) -> str:
    """Ensure all opened HTML tags are properly closed for Telegram.

    Fixes unclosed <pre>, <code>, <b>, etc. that cause BadRequest errors.
    """
    # Track open tags (only Telegram-supported ones)
    open_tags = []
    for m in re.finditer(r"<(/?)(\w[\w-]*)([^>]*)>", text):
        is_close = m.group(1) == "/"
        tag = m.group(2).lower()
        if tag not in _TG_TAGS:
            continue
        if is_close:
            # Remove matching open tag (innermost first)
            for i in range(len(open_tags) - 1, -1, -1):
                if open_tags[i] == tag:
                    open_tags.pop(i)
                    break
        else:
            open_tags.append(tag)

    # Close any unclosed tags in reverse order
    for tag in reversed(open_tags):
        text += f"</{tag}>"

    return text


def _format_tasks_telegram(tasks: list[dict]) -> str:
    """Format work engine tasks for Telegram HTML output."""
    if not tasks:
        return "<i>No tasks.</i>"

    icons = {"active": "🔄", "todo": "⬜", "done": "✅", "cancelled": "➖"}

    by_project: dict[str, list] = {}
    unassigned: list = []
    for t in tasks:
        proj = t.get("project")
        if proj:
            by_project.setdefault(proj, []).append(t)
        else:
            unassigned.append(t)

    lines: list[str] = []
    for proj, proj_tasks in sorted(by_project.items()):
        lines.append(f"\n<b>{proj.upper()}</b>")
        for t in sorted(proj_tasks, key=lambda x: x.get("priority", 9)):
            icon = icons.get(t.get("status", "todo"), "⬜")
            title = t.get("title", "Untitled")
            p = t.get("priority", 3)
            p_str = f" P{p}" if p and int(p) <= 2 else ""
            tid = t.get("id", "")
            lines.append(f"  {icon} {title}{p_str}  <i>{tid}</i>")

    if unassigned:
        lines.append(f"\n<b>UNASSIGNED</b>")
        for t in sorted(unassigned, key=lambda x: x.get("priority", 9)):
            icon = icons.get(t.get("status", "todo"), "⬜")
            title = t.get("title", "Untitled")
            tid = t.get("id", "")
            lines.append(f"  {icon} {title}  <i>{tid}</i>")

    return "\n".join(lines) if lines else "<i>No tasks.</i>"


class TelegramChannel:
    def __init__(self, bot_token: str, allowed_chat_id: int,
                 forum_group_id: int = None, topic_routes: dict = None):
        self.bot_token = bot_token
        self.allowed_chat_id = allowed_chat_id
        self.forum_group_id = forum_group_id
        # topic_routes: {thread_id: {"cwd": "/path/to/project", "agent": "nuchay"}}
        self.topic_routes = topic_routes or {}
        self.app: Application = None
        # Per-conversation locks — prevents concurrent Claude processes
        self._conversation_locks: dict[str, asyncio.Lock] = {}

    def _is_authorized(self, chat_id: int) -> bool:
        return chat_id == self.allowed_chat_id or chat_id == self.forum_group_id

    def _get_topic_config(self, message) -> dict:
        """Get routing config for a forum topic message."""
        thread_id = getattr(message, 'message_thread_id', None)
        if thread_id and thread_id in self.topic_routes:
            return self.topic_routes[thread_id]
        return {}

    # ── Reactions ─────────────────────────────────────────────

    async def _react(self, message, emoji: str):
        """Set a reaction emoji on a message. Silently falls back to typing."""
        try:
            await message.set_reaction(ReactionTypeEmoji(emoji))
        except Exception as e:
            logger.debug(f"Reaction '{emoji}' failed: {e}")
            if emoji == "👀":
                try:
                    await message.chat.send_action("typing")
                except Exception:
                    pass

    async def _ack_message(self, message):
        """React to a message with eyes to confirm receipt."""
        await self._react(message, "👀")

    async def _safe_delete(self, message):
        """Delete a message, silently ignoring errors."""
        try:
            await message.delete()
        except Exception:
            pass

    # ── Typing keepalive ────────────────────────────────────

    async def _typing_keepalive(self, chat, stop: asyncio.Event):
        """Re-send typing action every few seconds until stop is set."""
        while not stop.is_set():
            try:
                await chat.send_action("typing")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=TYPING_INTERVAL)
            except asyncio.TimeoutError:
                pass

    # ── Live streaming ───────────────────────────────────────

    def _find_split_point(self, markdown: str) -> int | None:
        """Find the best split point in markdown text.

        Returns the index to split at, or None if no good split exists.
        Only splits after MIN_CHUNK_BEFORE_SPLIT characters.
        Forces a split if text approaches the Telegram message limit.
        """
        if len(markdown) < MIN_CHUNK_BEFORE_SPLIT:
            return None

        # Hard ceiling: force split before hitting Telegram limit
        # Use a safety margin since HTML conversion can expand text
        hard_limit = TELEGRAM_MSG_LIMIT - 500

        # Search for split points after the minimum chunk size
        search_start = MIN_CHUNK_BEFORE_SPLIT
        best = None
        for m in _SPLIT_PATTERN.finditer(markdown, search_start):
            # Don't split if we're inside a code block
            before = markdown[:m.start()]
            open_fences = before.count("```")
            if open_fences % 2 == 1:
                continue  # inside a code block, skip
            best = m.start()
            # If we're past the hard limit, take this split immediately
            if m.start() >= hard_limit:
                break
            # If we found a split below hard_limit, keep searching for a better one
            # but stop if we're getting close to the limit
            if best and len(markdown) > hard_limit:
                break  # take the first valid split when content is long
            break  # take the first valid split

        # If no natural split found but text exceeds hard limit, force split at last newline
        if best is None and len(markdown) > hard_limit:
            cut = markdown.rfind("\n", MIN_CHUNK_BEFORE_SPLIT, hard_limit)
            if cut > 0:
                best = cut

        return best

    def _split_for_telegram(self, text: str) -> list[str]:
        """Split text into chunks that fit within Telegram's message limit.

        Tries to split at newlines to avoid breaking mid-sentence or mid-tag.
        """
        if len(text) <= TELEGRAM_MSG_LIMIT:
            return [text]

        chunks = []
        remaining = text
        while len(remaining) > TELEGRAM_MSG_LIMIT:
            # Find the last newline within the limit
            cut = remaining.rfind("\n", 0, TELEGRAM_MSG_LIMIT)
            if cut <= 0:
                # No newline found — try splitting at last space
                cut = remaining.rfind(" ", 0, TELEGRAM_MSG_LIMIT)
            if cut <= 0:
                # Hard cut as last resort
                cut = TELEGRAM_MSG_LIMIT

            chunk = remaining[:cut].rstrip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[cut:].lstrip("\n")

        if remaining.strip():
            chunks.append(remaining.strip())

        return chunks or [text]

    async def _send_html(self, chat, text: str, thread_id: int = None):
        """Send a message with HTML formatting, falling back to plain text.

        Automatically splits messages that exceed Telegram's 4096 char limit.
        Sanitizes HTML to close any unclosed tags before sending.
        """
        chunks = self._split_for_telegram(text)
        last_msg = None

        for chunk in chunks:
            chunk = _sanitize_html(chunk)
            kwargs = {"parse_mode": "HTML"}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            try:
                last_msg = await chat.send_message(chunk, **kwargs)
            except BadRequest as e:
                logger.warning(f"HTML send failed, falling back to plain: {e}")
                clean = re.sub(r"<[^>]+>", "", chunk)
                kwargs.pop("parse_mode", None)
                if clean.strip():
                    last_msg = await chat.send_message(clean, **kwargs)

        return last_msg

    async def _edit_html(self, msg, text: str):
        """Edit a message with HTML formatting, falling back to plain text."""
        text = _sanitize_html(text)
        try:
            await msg.edit_text(text, parse_mode="HTML")
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.warning(f"HTML edit failed ({e}), trying plain text")
            try:
                clean = re.sub(r"<[^>]+>", "", text)
                if clean.strip():
                    await msg.edit_text(clean)
            except BadRequest as e2:
                logger.warning(f"Plain text edit also failed: {e2}")
        except TimedOut:
            pass
        except Exception as e:
            logger.debug(f"Edit failed: {e}")

    async def _send_tool_status(self, chat, status_msg, description: str, thread_id: int = None):
        """Send or edit a tool status message (italic, lightweight)."""
        text = f"<i>{description}</i>"
        if status_msg is None:
            try:
                kwargs = {"parse_mode": "HTML"}
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                return await chat.send_message(text, **kwargs)
            except Exception as e:
                logger.debug(f"Tool status send failed: {e}")
                return None
        else:
            try:
                await status_msg.edit_text(text, parse_mode="HTML")
                return status_msg
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    logger.debug(f"Tool status edit failed: {e}")
                return status_msg
            except Exception:
                return status_msg

    async def _stream_response(self, chat, reply_to, message: str, user_key: str,
                               image_paths: list[str] | None = None,
                               cwd: str | None = None,
                               thread_id: int = None) -> str:
        """Send message to Claude and stream response to Telegram.

        Single-message flow: sends "Thinking..." with stop button immediately,
        then edits it progressively with tool status and streaming text.
        """
        start = _time.time()

        # Typing keepalive
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(self._typing_keepalive(chat, stop_typing))

        # Determine if this is a DM
        is_dm = (chat.id == self.allowed_chat_id and thread_id is None
                 and self.forum_group_id is None)

        # Send immediate thinking indicator with stop button
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        thinking_kwargs = {}
        if thread_id:
            thinking_kwargs["message_thread_id"] = thread_id
        stop_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("■ Stop", callback_data="stop_generation")
        ]])
        try:
            thinking_msg = await chat.send_message(
                "<i>Thinking...</i>", parse_mode="HTML",
                reply_markup=stop_keyboard,
                disable_notification=True, **thinking_kwargs,
            )
            thinking_msg_id = thinking_msg.message_id
        except Exception:
            thinking_msg_id = None

        had_error = False
        final_text = ""
        result = None

        try:
            # Start streaming from Claude
            agent_name, is_resumed, events = await stream_claude(
                message=message,
                user_key=user_key,
                cwd=cwd,
                image_paths=image_paths,
            )

            log_agent = agent_name or "claude"
            aid = log_activity(log_agent, "invoke", status="running",
                               summary=message[:100])

            # Render stream to Telegram — pass the thinking message so it gets
            # edited into the response (single message flow, no ghosts)
            final_text, result = await render_stream(
                bot=self.app.bot,
                chat_id=chat.id,
                events=events,
                thread_id=thread_id,
                is_dm=is_dm,
                is_resumed=is_resumed,
                initial_msg_id=thinking_msg_id,
                user_query=message,
            )

            duration_ms = int((_time.time() - start) * 1000)

            # Log completion
            if result and not result.is_error:
                from activity_client import update_activity
                update_activity(aid, "completed", summary=final_text[:100],
                                duration_ms=duration_ms)
                log_execution(
                    task=message[:200], approach="claude-cli-stream",
                    success=True, duration_ms=duration_ms,
                    agent=log_agent,
                    session_id=result.session_id[:16] if result.session_id else None,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                    num_turns=result.num_turns,
                    user_key=user_key,
                )
            else:
                had_error = True
                from activity_client import update_activity
                update_activity(aid, "failed",
                                summary=(result.text if result else "Unknown error")[:100],
                                duration_ms=duration_ms)
                log_execution(
                    task=message[:200], approach="claude-cli-stream",
                    success=False, duration_ms=duration_ms,
                    error=(result.text if result else "Unknown error")[:200],
                    agent=log_agent,
                    input_tokens=result.input_tokens if result else 0,
                    output_tokens=result.output_tokens if result else 0,
                    cost_usd=result.cost_usd if result else 0,
                    user_key=user_key,
                )

        except Exception as exc:
            had_error = True
            logger.error(f"Stream response failed: {exc}")
            final_text = final_text or f"Error: {exc}"
        finally:
            stop_typing.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        # Interactive buttons
        await self._send_keyboard(chat, final_text, user_key, thread_id=thread_id)

        # Final reaction: 👍 success or 💔 error
        if reply_to:
            await self._react(reply_to, "💔" if had_error else "👍")

        return final_text

    async def _send_keyboard(self, chat, response_text: str, user_key: str,
                              thread_id: int = None):
        """Detect options or add quick-action buttons after a response."""
        session_id = get_session_id(user_key) or ""

        detected = detect_options(response_text)
        if not detected:
            return

        kwargs = {}
        if thread_id:
            kwargs["message_thread_id"] = thread_id

        markup = build_option_keyboard(detected, session_id)
        try:
            # Send buttons silently — no extra notification buzz.
            # Uses a thin prompt instead of "Select an option:".
            await chat.send_message(
                "\u2193", reply_markup=markup,
                disable_notification=True, **kwargs,
            )
        except Exception as e:
            logger.debug(f"Option keyboard send failed: {e}")

    # ── Handlers ────────────────────────────────────────────

    def _queue_message_for_bus(self, update: Update):
        """Append incoming message to the comms bus queue file.

        Written as JSONL so the TelegramAdapter can read it during poll cycles.
        This is the bridge→comms layer handoff point.
        """
        try:
            msg = update.message
            if not msg or not msg.text:
                return
            queue_path = Path.home() / ".aos" / "data" / "telegram-messages.jsonl"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            from_user = msg.from_user
            record = {
                "id": f"tg-{msg.message_id}",
                "chat_id": msg.chat_id,
                "from_user": {
                    "id": from_user.id if from_user else None,
                    "first_name": from_user.first_name if from_user else "",
                    "last_name": from_user.last_name if from_user else "",
                    "username": from_user.username if from_user else "",
                },
                "text": msg.text,
                "timestamp": msg.date.timestamp() if msg.date else _time.time(),
                "from_me": False,
                "thread_id": getattr(msg, "message_thread_id", None),
            }
            with open(queue_path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.debug(f"Failed to queue message for bus: {e}")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        chat_id = update.message.chat_id
        if not self._is_authorized(chat_id):
            logger.warning(f"Unauthorized message from chat_id={chat_id}")
            return

        # Queue for comms bus (non-blocking, fire-and-forget)
        self._queue_message_for_bus(update)

        # Check if this is an evening check-in reply (intercept before Claude dispatch)
        text_raw = update.message.text.strip()
        if (is_awaiting_checkin_reply()
                and not was_checkin_replied()
                and not text_raw.startswith("/")
                and "," in text_raw
                and len(text_raw) < 500):
            try:
                _save_checkin_to_daily(text_raw)
                mark_checkin_replied()
                await update.message.reply_text(
                    "Evening check-in saved. Good night.",
                    parse_mode="HTML",
                )
                log_activity("telegram", "checkin_saved", summary=text_raw[:100])
                return
            except Exception as e:
                logger.warning(f"Failed to save check-in reply: {e}")
                # Fall through to normal message handling

        # Friction rules approval intercept (e.g., "/approve-rules 1 3" or "/approve-rules all")
        if text_raw.startswith("/approve-rules"):
            try:
                import subprocess as _sp
                args = text_raw.split()[1:]  # e.g., ["1", "3"] or ["all"]
                if not args:
                    await update.message.reply_text(
                        "Usage: /approve-rules 1 3 or /approve-rules all",
                        parse_mode="HTML",
                    )
                    return
                cmd = ["/opt/homebrew/bin/python3", str(Path(__file__).resolve().parent.parent.parent / "bin" / "friction-rules"), "apply"] + args
                result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    await update.message.reply_text(f"Error: {result.stderr[:500]}", parse_mode="HTML")
                else:
                    # The script sends its own Telegram notification, just log it
                    log_activity("telegram", "rules_approved", summary=f"Approved rules: {' '.join(args)}")
                return
            except Exception as e:
                logger.warning(f"Failed to process approve-rules: {e}")
                await update.message.reply_text(f"Error processing rule approval: {e}")
                return

        # Forum topic routing — use thread_id for session key and cwd
        topic_config = self._get_topic_config(update.message)
        thread_id = getattr(update.message, 'message_thread_id', None)
        if thread_id:
            user_key = f"telegram:{chat_id}:topic:{thread_id}"
        else:
            user_key = f"telegram:{chat_id}"
        topic_cwd = topic_config.get("cwd")
        topic_agent = topic_config.get("agent")
        text = update.message.text

        # ── Reply-to context — when user swipes to reply ──
        if update.message.reply_to_message and update.message.reply_to_message.text:
            quoted = update.message.reply_to_message.text[:500]
            text = f'[Replying to: "{quoted}"]\n\n{text}'

        # ── Quick command intercept ──────────────────────
        if not topic_agent:
            try:
                quick_reply = classify_intent(text)
                if quick_reply:
                    logger.info(f"Quick command: {text[:60]}")
                    log_activity("telegram", "quick_command", summary=text[:100])
                    await update.message.reply_text(
                        quick_reply, parse_mode="HTML",
                        message_thread_id=thread_id,
                    )
                    return
            except Exception as e:
                logger.warning(f"Quick command failed, falling through to Claude: {e}")

        # If topic has a dedicated agent, prepend dispatch
        if topic_agent and not text.lower().startswith(("ask ", "tell ", "@")):
            text = f"ask {topic_agent} to {text}"

        # Acknowledge receipt immediately (before lock — user sees 👀 right away)
        await self._ack_message(update.message)
        logger.info(f"Message: {text[:80]} [{user_key}]")
        log_activity("telegram", "message_received", summary=text[:100])

        # Per-conversation lock — only one Claude process at a time
        lock = self._conversation_locks.setdefault(user_key, asyncio.Lock())
        if lock.locked():
            # Another message is being processed — let user know
            _q_kwargs = {"disable_notification": True}
            if thread_id:
                _q_kwargs["message_thread_id"] = thread_id
            try:
                await update.message.chat.send_message("⏳", **_q_kwargs)
            except Exception:
                pass

        async with lock:
            _save_inflight(
                chat_id=update.message.chat_id,
                text=text,
                user_key=user_key,
                thread_id=thread_id,
                cwd=topic_cwd,
            )

            _conv_start = _time.monotonic()
            _topic_name = topic_agent or (f"topic:{thread_id}" if thread_id else "dm")
            _conv_id = log_conversation(
                user_key=user_key, agent=topic_agent, topic_name=_topic_name,
                message=update.message.text,
            )

            # ── Task dispatch: multi-step work runs in tmux, agent picks tools ──
            if _is_task_dispatch(text):
                logger.info(f"Task dispatch: {user_key} → {text[:60]}")
                log_activity("telegram", "task_dispatch", summary=text[:100])
                response = await _dispatch_steer_and_report(
                    update.message.chat, update.message, text,
                    thread_id=thread_id,
                )
            else:
                from session_manager import get_persistent_session
                _ps = get_persistent_session()
                _mode = "persistent" if _ps.alive else "new (starting persistent)"
                logger.info(f"Claude dispatch: {user_key} → {_mode}")

                response = await self._stream_response(
                    update.message.chat, update.message, text, user_key,
                    cwd=topic_cwd,
                    thread_id=thread_id,
                )

            _clear_inflight()

            _conv_duration = int((_time.monotonic() - _conv_start) * 1000)
            logger.info(f"Response: {_conv_duration}ms, {len(response)} chars [{user_key}]")
            update_conversation(_conv_id, response=response[:10000], duration_ms=_conv_duration)
            log_activity("telegram", "response_sent", summary=response[:100])

    async def _handle_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        chat_id = update.message.chat_id
        if not self._is_authorized(chat_id):
            return
        # Also clear topic-specific sessions if in a forum topic
        thread_id = getattr(update.message, 'message_thread_id', None)
        if thread_id:
            clear_session(f"telegram:{chat_id}:topic:{thread_id}")
        else:
            clear_session(f"telegram:{chat_id}")
        # Auto-commit any changes from the cleared session
        import subprocess as _sp
        _sp.Popen(
            [str(Path(__file__).resolve().parent.parent.parent / "bin" / "auto-commit"), "bridge"],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
        await update.message.reply_text("New session started.")

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.voice:
            return
        chat_id = update.message.chat_id
        if not self._is_authorized(chat_id):
            logger.warning(f"Unauthorized voice from chat_id={chat_id}")
            return

        # Forum topic routing
        topic_config = self._get_topic_config(update.message)
        thread_id = getattr(update.message, 'message_thread_id', None)
        if thread_id:
            user_key = f"telegram:{chat_id}:topic:{thread_id}"
        else:
            user_key = f"telegram:{chat_id}"
        topic_cwd = topic_config.get("cwd")
        topic_agent = topic_config.get("agent")

        # Acknowledge receipt
        await self._react(update.message, "🎙")
        log_activity("telegram", "voice_received", summary=f"voice {update.message.voice.duration}s")

        # Download and transcribe
        reply_kwargs = {}
        if thread_id:
            reply_kwargs["message_thread_id"] = thread_id

        try:
            voice_file = await update.message.voice.get_file()
            text = await transcribe_voice(voice_file)
        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            await self._react(update.message, "💔")
            await update.message.reply_text(f"Transcription failed: {e}", **reply_kwargs)
            bridge_event("voice_transcription_failed", level="error", error=str(e))
            return

        if not text or not text.strip():
            await self._react(update.message, "💔")
            await update.message.reply_text("(couldn't transcribe — empty audio)", **reply_kwargs)
            return

        # Show transcription — italic reply to the voice message, no labels.
        await self._react(update.message, "✅")
        try:
            await update.message.reply_text(
                f"<i>{text}</i>",
                parse_mode="HTML",
                **reply_kwargs,
            )
        except Exception as e:
            logger.warning(f"Transcript reply failed: {e}")
            # Fallback: send as regular message
            await update.message.chat.send_message(
                f"<i>{text}</i>", parse_mode="HTML", **reply_kwargs,
            )

        # Prepend transcript as context for Claude (it sees what you said).
        transcript_prefix = f"[Voice transcript: {text}]\n\n"
        prompt = transcript_prefix + text

        # If topic has a dedicated agent, prepend dispatch
        if topic_agent and not text.lower().startswith(("ask ", "tell ", "@")):
            prompt = f"ask {topic_agent} to {text}"

        # Stream Claude's response
        response = await self._stream_response(
            update.message.chat, update.message, prompt, user_key,
            cwd=topic_cwd, thread_id=thread_id,
        )
        log_activity("telegram", "voice_response_sent", summary=response[:100])

    async def _handle_whisper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Switch whisper mode: /whisper fast | /whisper accurate"""
        if not update.message:
            return
        if not self._is_authorized(update.message.chat_id):
            return
        args = context.args
        if not args:
            current = get_mode()
            await update.message.reply_text(
                f"Whisper mode: <b>{current}</b>\n\n"
                f"<b>fast</b> — base model, quick transcription\n"
                f"<b>accurate</b> — small model, better for mixed languages\n\n"
                f"Usage: /whisper fast | accurate",
                parse_mode="HTML",
            )
            return
        try:
            set_mode(args[0].lower())
            await update.message.reply_text(
                f"Whisper mode switched to: <b>{get_mode()}</b>",
                parse_mode="HTML",
            )
        except ValueError as e:
            await update.message.reply_text(str(e))

    async def _extract_keyframes(self, video_path: str, max_frames: int = 4) -> list[str]:
        """Extract keyframes from a video using ffmpeg. Returns list of image paths."""
        out_dir = tempfile.mkdtemp(prefix="tg_frames_")
        pattern = f"{out_dir}/frame_%02d.jpg"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", video_path,
                "-vf", f"fps=1/2,select='eq(pict_type\\,I)',scale=1280:-1",
                "-frames:v", str(max_frames),
                "-q:v", "2", "-y", pattern,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Keyframe extraction failed: {e}")
            # Fallback: grab a single frame at 1s
            try:
                proc2 = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-i", video_path, "-ss", "1",
                    "-frames:v", "1", "-q:v", "2", "-y",
                    f"{out_dir}/frame_01.jpg",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc2.wait(), timeout=15)
            except Exception:
                pass

        frames = sorted(Path(out_dir).glob("frame_*.jpg"))
        return [str(f) for f in frames]

    async def _handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photos, videos, and documents (images/video)."""
        msg = update.message
        if not msg:
            return
        chat_id = msg.chat_id
        if not self._is_authorized(chat_id):
            logger.warning(f"Unauthorized media from chat_id={chat_id}")
            return

        # Forum topic routing
        topic_config = self._get_topic_config(msg)
        thread_id = getattr(msg, 'message_thread_id', None)
        if thread_id:
            user_key = f"telegram:{chat_id}:topic:{thread_id}"
        else:
            user_key = f"telegram:{chat_id}"
        topic_cwd = topic_config.get("cwd")
        topic_agent = topic_config.get("agent")

        caption = msg.caption or ""
        image_paths: list[str] = []
        is_video = False

        # Acknowledge receipt immediately
        await self._ack_message(msg)

        # Show processing status for media that takes time
        media_status_msg = None
        if msg.video:
            media_status_msg = await msg.reply_text("🎬 Processing video...")
        elif msg.photo:
            media_status_msg = await msg.reply_text("📎 Processing image...")

        try:
            if msg.photo:
                # Photos: download the largest size
                photo = msg.photo[-1]
                tg_file = await photo.get_file()
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".jpg", prefix="tg_photo_", delete=False
                )
                tmp.close()
                await tg_file.download_to_drive(tmp.name)
                image_paths.append(tmp.name)
                log_activity("telegram", "photo_received", summary=caption[:100] or "photo")

            elif msg.video:
                is_video = True
                tg_file = await msg.video.get_file()
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".mp4", prefix="tg_video_", delete=False
                )
                tmp.close()
                await tg_file.download_to_drive(tmp.name)
                # Extract keyframes for Claude to view
                image_paths = await self._extract_keyframes(tmp.name)
                if not image_paths:
                    await msg.reply_text("Couldn't extract frames from video.")
                    return
                log_activity("telegram", "video_received",
                             summary=f"video {msg.video.duration}s — {caption[:80]}")

            elif msg.document:
                mime = msg.document.mime_type or ""
                if mime.startswith("image/"):
                    ext = mime.split("/")[-1].replace("jpeg", "jpg")
                    tg_file = await msg.document.get_file()
                    tmp = tempfile.NamedTemporaryFile(
                        suffix=f".{ext}", prefix="tg_doc_", delete=False
                    )
                    tmp.close()
                    await tg_file.download_to_drive(tmp.name)
                    image_paths.append(tmp.name)
                    log_activity("telegram", "image_doc_received",
                                 summary=caption[:100] or "image document")
                elif mime.startswith("video/"):
                    is_video = True
                    tg_file = await msg.document.get_file()
                    tmp = tempfile.NamedTemporaryFile(
                        suffix=".mp4", prefix="tg_vdoc_", delete=False
                    )
                    tmp.close()
                    await tg_file.download_to_drive(tmp.name)
                    image_paths = await self._extract_keyframes(tmp.name)
                    if not image_paths:
                        await msg.reply_text("Couldn't extract frames from video.")
                        return
                    log_activity("telegram", "video_doc_received",
                                 summary=f"video doc — {caption[:80]}")
                else:
                    await msg.reply_text(
                        f"Unsupported file type: {mime}\n"
                        "I can handle photos, videos, and image files."
                    )
                    return
            else:
                return

        except Exception as e:
            logger.error(f"Media download failed: {e}")
            if media_status_msg:
                await self._safe_delete(media_status_msg)
            await self._react(msg, "💔")
            await msg.reply_text(f"Failed to download media: {e}")
            return

        # Clear processing status
        if media_status_msg:
            await self._safe_delete(media_status_msg)

        if not image_paths:
            await self._react(msg, "💔")
            await msg.reply_text("Couldn't process the media file.")
            return

        # Build prompt
        media_type = "video (keyframes extracted)" if is_video else "photo"
        prompt = caption if caption else f"The user sent a {media_type}. Describe what you see."

        # If topic has a dedicated agent, prepend dispatch
        if topic_agent and not prompt.lower().startswith(("ask ", "tell ", "@")):
            prompt = f"ask {topic_agent} to {prompt}"

        # Stream response (live HTML streaming)
        response = await self._stream_response(
            msg.chat, msg, prompt, user_key, image_paths=image_paths,
            cwd=topic_cwd, thread_id=thread_id,
        )
        log_activity("telegram", "media_response_sent", summary=response[:100])

        # Clean up temp files
        for p in image_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
            # Also clean up parent dir if it was a keyframes dir
            parent = Path(p).parent
            if parent.name.startswith("tg_frames_"):
                try:
                    for f in parent.iterdir():
                        f.unlink(missing_ok=True)
                    parent.rmdir()
                except OSError:
                    pass

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard button taps."""
        query = update.callback_query
        if not query:
            return

        chat_id = query.message.chat_id
        if not self._is_authorized(chat_id):
            await query.answer("Unauthorized")
            return

        # ── Stop button — kill the active Claude process ──
        if query.data == "stop_generation":
            thread_id = getattr(query.message, 'message_thread_id', None)
            user_key = f"telegram:{chat_id}:topic:{thread_id}" if thread_id else f"telegram:{chat_id}"
            cancelled = cancel_stream(user_key)
            await query.answer("Stopping..." if cancelled else "Nothing running")
            # Don't edit the message here — the renderer will handle final delivery
            # when the process dies and the generator ends.
            return

        await query.answer()  # dismiss the loading spinner

        result = resolve_callback(query.data)
        if not result:
            try:
                await query.edit_message_text(
                    "This button is from an older session.\n"
                    "Send your message again to get fresh options."
                )
            except Exception:
                pass
            bridge_event("button_expired", level="info",
                         callback_data=query.data[:30])
            return

        kind, message_text = result

        # Show what was selected and remove buttons
        if kind == "option":
            try:
                await query.edit_message_text(f"Selected: {message_text}")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(f"{message_text}")
            except Exception:
                pass

        # Determine user_key (check for forum topic thread)
        thread_id = getattr(query.message, 'message_thread_id', None)
        if thread_id:
            user_key = f"telegram:{chat_id}:topic:{thread_id}"
        else:
            user_key = f"telegram:{chat_id}"

        topic_config = self._get_topic_config(query.message) if thread_id else {}
        topic_cwd = topic_config.get("cwd")

        # Send to Claude via the existing streaming path
        response = await self._stream_response(
            query.message.chat, query.message, message_text, user_key,
            cwd=topic_cwd, thread_id=thread_id,
        )
        log_activity("telegram", "callback_response", summary=response[:100])

    # ── Vault commands (/note, /search, /capture) ─────────

    VAULT_ROOT = Path.home() / "vault"
    QMD_BIN = Path.home() / ".bun" / "bin" / "qmd"

    def _qmd_reindex_async(self):
        """Trigger QMD re-index in background (non-blocking)."""
        if self.QMD_BIN.exists():
            try:
                qmd_env = {**os.environ, "PATH": f"{self.QMD_BIN.parent}:{os.environ.get('PATH', '')}"}
                subprocess.Popen(
                    [str(self.QMD_BIN), "update"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=qmd_env,
                )
                subprocess.Popen(
                    [str(self.QMD_BIN), "embed"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=qmd_env,
                )
            except Exception:
                pass

    async def _handle_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save a quick note to the vault. Usage: /note <text>"""
        if not update.message or not self._is_authorized(update.message.chat_id):
            return
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text(
                "<b>Usage:</b> <code>/note your thought here</code>",
                parse_mode="HTML",
            )
            return

        import datetime
        now = datetime.datetime.now()
        slug = re.sub(r'[^\w\s-]', '', text[:40]).strip().replace(' ', '-').lower()
        filename = f"{now.strftime('%Y%m%d-%H%M')}-{slug}.md"
        filepath = self.VAULT_ROOT / "ideas" / filename

        content = (
            f"---\n"
            f"date: \"{now.strftime('%Y-%m-%d')}\"\n"
            f"time: \"{now.strftime('%H:%M')}\"\n"
            f"type: idea\n"
            f"source: telegram\n"
            f"tags: []\n"
            f"---\n\n"
            f"{text}\n"
        )

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        self._qmd_reindex_async()
        log_activity("telegram", "note_saved", summary=text[:100])
        await update.message.reply_text(
            f"Saved to <code>ideas/{filename}</code>",
            parse_mode="HTML",
        )

    async def _handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search the knowledge vault via QMD. Usage: /search <query>"""
        if not update.message or not self._is_authorized(update.message.chat_id):
            return
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text(
                "<b>Usage:</b> <code>/search your query here</code>",
                parse_mode="HTML",
            )
            return

        if not self.QMD_BIN.exists():
            await update.message.reply_text("QMD not installed. Run: bun install -g @tobilu/qmd")
            return

        await update.message.chat.send_action("typing")
        try:
            result = subprocess.run(
                [str(self.QMD_BIN), "query", query, "-n", "5"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PATH": f"{self.QMD_BIN.parent}:{os.environ.get('PATH', '')}"},
            )
            output = result.stdout.strip()
            if not output:
                await update.message.reply_text("No results found.")
                return

            # Format for Telegram (truncate long output)
            if len(output) > 3000:
                output = output[:3000] + "\n..."
            await update.message.reply_text(
                f"<b>Search:</b> <i>{query}</i>\n\n<pre>{output}</pre>",
                parse_mode="HTML",
            )
        except subprocess.TimeoutExpired:
            await update.message.reply_text("Search timed out.")
        except Exception as e:
            await update.message.reply_text(f"Search failed: {e}")

    async def _handle_capture(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Universal capture — detect type and route to vault. Usage: /capture <text or URL>"""
        if not update.message or not self._is_authorized(update.message.chat_id):
            return
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text(
                "<b>Usage:</b> <code>/capture your thought, URL, or task</code>\n\n"
                "Auto-detects: YouTube links, URLs, tasks (todo:/task:), ideas",
                parse_mode="HTML",
            )
            return

        import datetime
        now = datetime.datetime.now()

        # Classify input type
        youtube_re = re.compile(r'(youtube\.com/watch\?v=|youtu\.be/)')
        url_re = re.compile(r'https?://')
        task_re = re.compile(r'^(todo|task|remind)[\s:]+', re.IGNORECASE)

        if youtube_re.search(text):
            folder = "materials/video-research"
            note_type = "youtube"
            slug = "youtube-" + now.strftime('%Y%m%d-%H%M')
        elif task_re.match(text):
            folder = "ideas"
            note_type = "task"
            text = task_re.sub('', text).strip()
            slug = "task-" + re.sub(r'[^\w\s-]', '', text[:30]).strip().replace(' ', '-').lower()
        elif url_re.search(text):
            folder = "materials"
            note_type = "bookmark"
            slug = "link-" + now.strftime('%Y%m%d-%H%M')
        else:
            folder = "ideas"
            note_type = "idea"
            slug = re.sub(r'[^\w\s-]', '', text[:40]).strip().replace(' ', '-').lower()

        filename = f"{now.strftime('%Y%m%d-%H%M')}-{slug}.md"
        filepath = self.VAULT_ROOT / folder / filename

        content = (
            f"---\n"
            f"date: \"{now.strftime('%Y-%m-%d')}\"\n"
            f"time: \"{now.strftime('%H:%M')}\"\n"
            f"type: {note_type}\n"
            f"source: telegram\n"
            f"tags: []\n"
            f"---\n\n"
            f"{text}\n"
        )

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

        # Post-capture: search for related content
        related_msg = ""
        if self.QMD_BIN.exists():
            try:
                result = subprocess.run(
                    [str(self.QMD_BIN), "search", text[:100], "-n", "3"],
                    capture_output=True, text=True, timeout=15,
                    env={**os.environ, "PATH": f"{self.QMD_BIN.parent}:{os.environ.get('PATH', '')}"},
                )
                if result.stdout.strip():
                    # Extract just filenames from output
                    lines = result.stdout.strip().split('\n')
                    related = [l.strip() for l in lines[:3] if l.strip()]
                    if related:
                        related_msg = "\n\n<b>Related:</b>\n" + "\n".join(
                            f"  • <code>{r[:80]}</code>" for r in related
                        )
            except Exception:
                pass

        # Post-capture: append wiki-links to related vault notes
        if self.QMD_BIN.exists():
            try:
                link_result = subprocess.run(
                    [str(self.QMD_BIN), "search", text[:80], "-n", "3", "--files"],
                    capture_output=True, text=True, timeout=15,
                    env={**os.environ, "PATH": f"{self.QMD_BIN.parent}:{os.environ.get('PATH', '')}"},
                )
                if link_result.stdout.strip():
                    links = []
                    for line in link_result.stdout.strip().split('\n'):
                        parts = line.split(',')
                        if len(parts) >= 3:
                            raw_path = parts[2].strip().strip('"')
                            if 'qmd://' in raw_path:
                                segments = raw_path.replace('qmd://', '').split('/')
                                note_stem = Path(segments[-1]).stem if segments else ""
                                if note_stem and note_stem not in filename:
                                    links.append(f"[[{note_stem}]]")
                    if links:
                        existing = filepath.read_text()
                        link_section = "\n\n## Related\n\n" + "\n".join(f"- {l}" for l in links[:5]) + "\n"
                        filepath.write_text(existing.rstrip() + link_section)
            except Exception:
                pass

        self._qmd_reindex_async()
        log_activity("telegram", "capture_saved", summary=f"{note_type}: {text[:100]}")
        await update.message.reply_text(
            f"Captured [{note_type}] → <code>{folder}/{filename}</code>{related_msg}",
            parse_mode="HTML",
        )

        # If YouTube, trigger transcription in the background
        if note_type == "youtube":
            asyncio.create_task(
                self._transcribe_youtube_capture(update.message.chat, text, filepath, thread_id)
            )

    async def _transcribe_youtube_capture(self, chat, url_text: str, note_path: Path,
                                             thread_id: int = None):
        """Background: transcribe a YouTube URL and append transcript to the vault note."""
        # Extract YouTube URL from the text
        yt_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', url_text)
        if not yt_match:
            return

        yt_url = yt_match.group(1)
        transcriber = WORKSPACE / "apps" / "transcriber" / "transcribe.py"

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(transcriber), yt_url,
                "--output", str(WORKSPACE / "apps" / "transcriber" / "output"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode()

            # Find the saved transcript file from output
            match = re.search(r'Transcript saved to: (.+\.txt)', output)
            if match and proc.returncode == 0:
                transcript_path = Path(match.group(1).strip())
                if transcript_path.exists():
                    transcript = transcript_path.read_text()
                    # Append transcript to the vault note
                    existing = note_path.read_text()
                    note_path.write_text(
                        existing.rstrip() + "\n\n## Transcript\n\n" + transcript + "\n"
                    )
                    kwargs = {}
                    if thread_id:
                        kwargs["message_thread_id"] = thread_id
                    await chat.send_message(
                        f"Transcript ready ({len(transcript)} chars) → <code>{note_path.name}</code>",
                        parse_mode="HTML", **kwargs,
                    )
                    return

            # If transcription failed, notify
            logger.warning(f"YouTube transcription failed: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            logger.warning(f"YouTube transcription timed out for {yt_url}")
        except Exception as e:
            logger.warning(f"YouTube transcription error: {e}")

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        chat_id = update.message.chat_id
        if not self._is_authorized(chat_id):
            return
        await update.message.reply_text("Bridge is running. Send any message to interact with the agent.")

    async def _handle_read(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Publish a .md file to Telegraph for Instant View reading."""
        if not update.message:
            return
        chat_id = update.message.chat_id
        if not self._is_authorized(chat_id):
            return

        if not context.args:
            # List available .md files
            from pathlib import Path
            workspace = Path.home() / "aos"
            md_files = sorted(
                [str(p.relative_to(workspace)) for p in workspace.rglob("*.md")
                 if not any(part.startswith(".") or part in ("node_modules", "vendor", ".venv")
                           for part in p.relative_to(workspace).parts)]
            )[:20]
            listing = "\n".join(f"  <code>{f}</code>" for f in md_files)
            await update.message.reply_text(
                f"<b>Usage:</b> <code>/read path/to/file.md</code>\n\n"
                f"<b>Available files:</b>\n{listing}",
                parse_mode="HTML",
            )
            return

        filepath = context.args[0]
        full_path = str(Path.home() / "aos" / filepath)

        from pathlib import Path
        p = Path(full_path)
        if not p.exists():
            # Try common locations
            for prefix in ["docs/", "specs/", "config/", ""]:
                alt = Path.home() / "aos" / f"{prefix}{filepath}"
                if alt.exists():
                    p = alt
                    break

        if not p.exists() or not p.is_file():
            await update.message.reply_text(f"File not found: <code>{filepath}</code>", parse_mode="HTML")
            return

        try:
            from telegraph_publisher import publish_md
            content = p.read_text()
            title = p.stem.replace("-", " ").replace("_", " ").title()
            url = publish_md(title, content)
            await update.message.reply_text(
                f'<a href="{url}">{title}</a>',
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
        except Exception as e:
            logger.error(f"Telegraph publish failed: {e}")
            await update.message.reply_text(f"Failed to publish: {e}")

    async def _handle_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /tasks command — vault-based task management.

        Usage:
          /tasks             — show active tasks (not done)
          /tasks all         — show everything including done
          /tasks add <name>  — create a new task
          /tasks do <name>   — start a task
          /tasks done <name> — mark as done
        """
        if not update.message:
            return
        if not self._is_authorized(update.message.chat_id):
            return

        from activity_client import log_activity

        args = context.args or []
        thread_id = getattr(update.message, 'message_thread_id', None)
        kwargs = {}
        if thread_id:
            kwargs["message_thread_id"] = thread_id

        work_cli = str(Path.home() / "aos" / "core" / "work" / "cli.py")

        def _run(*cmd_args):
            try:
                result = subprocess.run(
                    ["python3", work_cli] + list(cmd_args),
                    capture_output=True, text=True, timeout=15,
                )
                return result.stdout.strip(), result.returncode == 0
            except Exception as e:
                return str(e), False

        if not args:
            await update.message.chat.send_action("typing")
            out, ok = _run("list", "--json")
            if ok and out:
                try:
                    tasks = json.loads(out)
                    msg = _format_tasks_telegram(tasks)
                except Exception:
                    msg = "<i>Could not parse tasks.</i>"
            else:
                msg = "<i>No tasks found.</i>"
            await update.message.reply_text(
                f"<b>Tasks</b>\n{msg}", parse_mode="HTML", **kwargs,
            )

        elif args[0].lower() == "all":
            await update.message.chat.send_action("typing")
            out, ok = _run("list", "--all", "--json")
            if ok and out:
                try:
                    tasks = json.loads(out)
                    msg = _format_tasks_telegram(tasks)
                except Exception:
                    msg = "<i>Could not parse tasks.</i>"
            else:
                msg = "<i>No tasks found.</i>"
            await update.message.reply_text(
                f"<b>All Tasks</b>\n{msg}", parse_mode="HTML", **kwargs,
            )

        elif args[0].lower() == "add" and len(args) > 1:
            task_name = " ".join(args[1:])
            topic_config = self._get_topic_config(update.message)
            topic_agent = topic_config.get("agent")
            project = "nuchay" if topic_agent == "nuchay" else "aos"
            out, ok = _run("add", task_name, "--project", project)
            if ok:
                await update.message.reply_text(
                    f"✅ {out}", parse_mode="HTML", **kwargs,
                )
                log_activity("telegram", "task_created", summary=task_name[:100])
            else:
                await update.message.reply_text(
                    f"Failed to create task.", **kwargs,
                )

        elif args[0].lower() == "done" and len(args) > 1:
            search = " ".join(args[1:])
            out, ok = _run("done", search)
            if ok:
                await update.message.reply_text(
                    f"✅ {out}", parse_mode="HTML", **kwargs,
                )
                log_activity("telegram", "task_done", summary=search[:100])
            else:
                await update.message.reply_text(
                    f"No task matching '<b>{search}</b>'.",
                    parse_mode="HTML", **kwargs,
                )

        elif args[0].lower() == "do" and len(args) > 1:
            search = " ".join(args[1:])
            out, ok = _run("start", search)
            if ok:
                await update.message.reply_text(
                    f"🔄 {out}", parse_mode="HTML", **kwargs,
                )
                log_activity("telegram", "task_started", summary=search[:100])
            else:
                await update.message.reply_text(
                    f"No task matching '<b>{search}</b>'.",
                    parse_mode="HTML", **kwargs,
                )

        elif args[0].lower() == "focus" and len(args) > 1:
            search = " ".join(args[1:])
            out, ok = _run("start", search)
            if ok:
                await update.message.reply_text(
                    f"🎯 {out}", parse_mode="HTML", **kwargs,
                )
                log_activity("telegram", "task_focused", summary=search[:100])
            else:
                await update.message.reply_text(
                    f"No task matching '<b>{search}</b>'.",
                    parse_mode="HTML", **kwargs,
                )

        else:
            await update.message.reply_text(
                "<b>Usage:</b>\n"
                "  /tasks — show active tasks\n"
                "  /tasks all — show everything\n"
                "  /tasks add &lt;name&gt; — create task\n"
                "  /tasks do &lt;name&gt; — start task\n"
                "  /tasks done &lt;name&gt; — complete task\n"
                "  /tasks focus &lt;name&gt; — focus on task",
                parse_mode="HTML", **kwargs,
            )

    async def _replay_inflight(self, app: Application):
        """Post-init: replay crashed messages + start API server."""
        # ── Start Bridge API server (for Mission Control) ──
        if hasattr(self, '_api_start'):
            try:
                await self._api_start()
                # Register bot so MC messages forward to Telegram
                from api_server import set_telegram_bot
                set_telegram_bot(app.bot, self.allowed_chat_id)
                logger.info("Bridge API server started on :4098")
            except Exception as e:
                logger.warning(f"Bridge API failed to start: {e}", exc_info=True)

        # ── Replay in-flight message from crash ──
        pending = _load_inflight()
        if not pending or not pending.get("text"):
            _clear_inflight()
            return

        ts = pending.get("timestamp", 0)
        if _time.time() - ts > 600:
            logger.info(f"Discarding stale inflight message ({int(_time.time() - ts)}s old)")
            _clear_inflight()
            return

        chat_id = pending["chat_id"]
        text = pending["text"]
        user_key = pending["user_key"]
        thread_id = pending.get("thread_id")
        cwd = pending.get("cwd")

        logger.info(f"Replaying in-flight message after restart: {text[:80]}")

        try:
            chat = await app.bot.get_chat(chat_id)
            resume_kwargs = {"parse_mode": "HTML"}
            if thread_id:
                resume_kwargs["message_thread_id"] = thread_id
            await app.bot.send_message(
                chat_id,
                "<i>Bridge restarted — resuming your request...</i>",
                **resume_kwargs,
            )
            response = await self._stream_response(
                chat, None, text, user_key,
                cwd=cwd, thread_id=thread_id,
            )
            _clear_inflight()
            log_activity("telegram", "inflight_replayed", summary=response[:100])
        except Exception as e:
            logger.error(f"Failed to replay in-flight message: {e}")
            _clear_inflight()

    def start(self):
        """Start the Telegram bot (blocking)."""
        # Global error handler — registered FIRST so no errors are ever unhandled
        async def _error_handler(update, context):
            logger.error(f"Unhandled error: {context.error}", exc_info=context.error)
            if update and update.effective_chat:
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="Something went wrong processing your message. Please try again.",
                    )
                except Exception:
                    pass

        self.app = (
            Application.builder()
            .token(self.bot_token)
            .concurrent_updates(True)
            .post_init(self._replay_inflight)
            .build()
        )

        # Error handler before any message handlers
        self.app.add_error_handler(_error_handler)

        self.app.add_handler(CommandHandler("new", self._handle_new))
        self.app.add_handler(CommandHandler("status", self._handle_status))
        self.app.add_handler(CommandHandler("tasks", self._handle_tasks))
        self.app.add_handler(CommandHandler("read", self._handle_read))
        self.app.add_handler(CommandHandler("whisper", self._handle_whisper))
        self.app.add_handler(CommandHandler("note", self._handle_note))
        self.app.add_handler(CommandHandler("search", self._handle_search))
        self.app.add_handler(CommandHandler("capture", self._handle_capture))
        self.app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self.app.add_handler(MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO,
            self._handle_media,
        ))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        logger.info(f"Telegram channel started (chat_id={self.allowed_chat_id})")
        self.app.run_polling(drop_pending_updates=True)
