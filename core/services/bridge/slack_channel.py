"""Slack channel — Socket Mode bot, routes DMs to Claude, sends responses."""

import json
import logging
import subprocess
import sys
from pathlib import Path

from conversation_store import record_inbound, record_outbound
from session_manager import WORKSPACE, clear_session, get_session_id, save_session_id
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logger = logging.getLogger("aos.bridge.slack_channel")

SLACK_MSG_LIMIT = 4000

# claude_lanes.py (core/infra/lib/) — loaded by repo-root-relative path, not
# ~/aos, so this resolves to whatever checkout this file itself is running
# from (the bridge's own venv has no package context for a dotted import).
# See core/engine/notify/router.py for the same trick. Falls back to a plain
# subprocess.run — identical to this module's pre-aos#244.3 behavior — if the
# lanes module cannot be loaded at all (e.g. an older ~/aos on this machine).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "lib"))
try:
    import claude_lanes
except Exception:  # noqa: BLE001 — a missing lanes module must not break Slack
    claude_lanes = None


def _run_claude(cmd, *, timeout, cwd):
    if claude_lanes is not None:
        return claude_lanes.run(cmd, timeout=timeout, cwd=cwd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _ask_claude_sync(message: str, user_key: str) -> str:
    """Synchronous Claude call for Slack (simple subprocess)."""
    cmd = [
        "claude", "-p", message,
        "--output-format", "json",
        "--max-turns", "25",
    ]
    session_id = get_session_id(user_key)
    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        result = _run_claude(cmd, timeout=300, cwd=str(WORKSPACE))
        if result.returncode == 0:
            data = json.loads(result.stdout)
            new_session_id = data.get("session_id")
            if new_session_id:
                save_session_id(user_key, new_session_id)
            return data.get("result", "(empty response)")
        else:
            return f"Error: {result.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "Request timed out (5 min limit)."
    except Exception as e:
        return f"Error: {e}"


class SlackChannel:
    def __init__(self, bot_token: str, app_token: str, allowed_user_id: str):
        self.allowed_user_id = allowed_user_id
        self.slack_app = App(token=bot_token)
        self.app_token = app_token
        self._register_handlers()

    def _is_authorized(self, user_id: str) -> bool:
        return user_id == self.allowed_user_id

    def _register_handlers(self):
        @self.slack_app.event("message")
        def handle_message(event, say):
            user_id = event.get("user", "")
            text = event.get("text", "")

            if not text or not self._is_authorized(user_id):
                if not self._is_authorized(user_id) and user_id:
                    logger.warning(f"Unauthorized Slack message from user={user_id}")
                return

            # Handle commands
            if text.strip().lower() == "/new":
                clear_session(f"slack:{user_id}")
                say("Session cleared. Starting fresh.")
                return

            if text.strip().lower() == "/status":
                say("Bridge is running. Send any message to interact with the agent.")
                return

            user_key = f"slack:{user_id}"
            record_inbound(text, kind="message", meta={"channel": "slack"})

            response = _ask_claude_sync(text, user_key)

            # Split long messages
            for i in range(0, len(response), SLACK_MSG_LIMIT):
                chunk = response[i:i + SLACK_MSG_LIMIT]
                say(chunk)

            record_outbound(response, kind="response", meta={"channel": "slack"})

    def start(self):
        """Start Slack Socket Mode with connection retry limit."""
        logger.info(f"Slack channel started (user={self.allowed_user_id})")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                handler = SocketModeHandler(self.slack_app, self.app_token)
                handler.start()
                return
            except Exception as e:
                logger.warning(f"Slack connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(5 * (attempt + 1))
        logger.error("Slack failed to connect after retries — disabling Slack channel")
