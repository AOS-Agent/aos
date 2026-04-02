"""Persistent Claude session — one long-running process, multiple messages.

Instead of spawning a new claude process per message (10-15s startup each time),
this keeps ONE process alive and pipes messages via --input-format stream-json.
Cold start happens once. Every subsequent message goes straight to the API (3-5s).

If the process dies, it auto-restarts with --resume to recover conversation.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from bridge_events import bridge_event

logger = logging.getLogger("bridge.persistent_session")


def _get_operator_name() -> str:
    try:
        import yaml
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            return (yaml.safe_load(op_file.read_text()) or {}).get("name", "")
    except Exception:
        pass
    return ""


def _get_default_agent() -> str:
    try:
        import yaml
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            return (yaml.safe_load(op_file.read_text()) or {}).get("agent_name", "chief")
    except Exception:
        pass
    return "chief"


class PersistentSession:
    """One long-running Claude process. Messages pipe in, responses stream out.

    Usage:
        session = PersistentSession()
        await session.start()

        async for event in session.send("hello"):
            handle(event)  # same StreamEvent types as session_manager
    """

    def __init__(self, cwd: str | None = None):
        self.cwd = cwd or str(Path.home())
        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self._start_lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, resume_session_id: str | None = None):
        """Start the persistent Claude process. Called once on bridge boot."""
        async with self._start_lock:
            if self.alive:
                return

            agent = _get_default_agent()
            op_name = _get_operator_name()
            greeting = f" You talk to the operator ({op_name})" if op_name else " You talk to the operator"

            cmd = [
                "claude", "-p",
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--permission-mode", "bypassPermissions",
                "--max-turns", "100",
                "--max-budget-usd", "50",
                "--append-system-prompt",
                f"You are {agent.capitalize()}, the AOS orchestrator.{greeting} "
                "and get things done — by taking direct action, delegating to specialist "
                "agents, or querying data sources. Be concise. Lead with the answer. "
                "You are running via Telegram. Prefer autonomous decisions for routine work. "
                "If you genuinely need the operator's input (choosing between fundamentally "
                "different approaches, confirming a destructive action, or clarifying ambiguity), "
                "ask — present clear numbered options when possible. The operator can reply and "
                "your next message continues the conversation. Don't ask for confirmation on "
                "routine tasks. If you need to delegate, use the Agent tool with subagent_type: "
                "steward (system health), advisor (analysis), or other installed agents. "
                "You have full tool access.",
            ]

            if resume_session_id:
                cmd.extend(["--resume", resume_session_id])
                logger.info(f"Persistent session resuming: {resume_session_id[:12]}")

            logger.info(f"Starting persistent Claude process (agent={agent})")
            t0 = time.time()

            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                    limit=16 * 1024 * 1024,
                )
            except FileNotFoundError:
                logger.error("claude CLI not found")
                return

            startup_ms = int((time.time() - t0) * 1000)
            logger.info(f"Persistent session started (pid={self.proc.pid}, {startup_ms}ms)")
            bridge_event("persistent_session_started", pid=self.proc.pid,
                         startup_ms=startup_ms)

    async def send(self, message: str, image_paths: list[str] | None = None):
        """Send a message and yield StreamEvent objects until the response completes.

        Reads directly from stdout — no background reader, no race conditions.
        """
        from session_manager import SessionInit, SessionResult, parse_event

        # Auto-start if not alive
        if not self.alive:
            await self.start(resume_session_id=self.session_id)

        if not self.alive:
            yield SessionResult(
                session_id="", text="Persistent session failed to start.",
                is_error=True, duration_ms=0, cost_usd=0,
                input_tokens=0, output_tokens=0, num_turns=0,
            )
            return

        # Build message text
        text = message
        if image_paths:
            img_lines = "\n".join(f"- Read the image file at: {p}" for p in image_paths)
            text = (
                f"{message or 'The user sent media without a caption.'}\n\n"
                f"[Attached media — use the Read tool to view these files]\n{img_lines}"
            )

        # Write NDJSON to stdin
        msg_json = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        })

        try:
            self.proc.stdin.write((msg_json + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.error(f"Failed to write to persistent session: {e}")
            yield SessionResult(
                session_id=self.session_id or "",
                text="Session crashed. Will restart on next message.",
                is_error=True, duration_ms=0, cost_usd=0,
                input_tokens=0, output_tokens=0, num_turns=0,
            )
            return

        # Read stdout line-by-line until we get a result event.
        # Uses readline() instead of async-for to avoid closing the stdout
        # iterator between calls — keeps the process alive across messages.
        try:
            while True:
                raw_line = await self.proc.stdout.readline()
                if not raw_line:
                    # EOF — process died
                    yield SessionResult(
                        session_id=self.session_id or "",
                        text="Session ended unexpectedly. Will restart on next message.",
                        is_error=True, duration_ms=0, cost_usd=0,
                        input_tokens=0, output_tokens=0, num_turns=0,
                    )
                    return

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = parse_event(line)
                if event is None:
                    continue

                # Track session ID for resume-on-crash
                if isinstance(event, SessionInit):
                    self.session_id = event.session_id
                elif isinstance(event, SessionResult):
                    self.session_id = event.session_id or self.session_id

                yield event

                # SessionResult = end of this response, return control
                if isinstance(event, SessionResult):
                    return

        except Exception as e:
            logger.error(f"Read error from persistent session: {e}")
            yield SessionResult(
                session_id=self.session_id or "",
                text=f"Session error: {e}",
                is_error=True, duration_ms=0, cost_usd=0,
                input_tokens=0, output_tokens=0, num_turns=0,
            )

    async def cancel(self) -> bool:
        """Kill current generation and restart the process."""
        if not self.alive:
            return False

        logger.info(f"Cancelling persistent session (pid={self.proc.pid})")
        self.proc.terminate()
        bridge_event("persistent_session_cancelled",
                      session_id=(self.session_id or "")[:12])
        # Next send() call will auto-restart via self.alive check
        return True

    async def stop(self):
        """Shut down the persistent session cleanly."""
        if self.alive:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
        logger.info("Persistent session stopped")
