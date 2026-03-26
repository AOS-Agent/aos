"""Session manager — spawns Claude Code processes and parses streaming events."""

import asyncio
import fcntl
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

from bridge_events import bridge_event

logger = logging.getLogger("bridge.session_manager")

WORKSPACE = Path.home() / "aos"
SESSIONS_FILE = WORKSPACE / "data" / "bridge" / "sessions.json"
AGENTS_DIR = WORKSPACE / ".claude" / "agents"

# ── Active process registry — enables stop button ────────────
# Maps user_key → asyncio.subprocess.Process for in-flight Claude calls.
# cancel_stream() sends SIGTERM; the generator handles cleanup naturally.
_active_processes: dict[str, asyncio.subprocess.Process] = {}


def cancel_stream(user_key: str) -> bool:
    """Cancel an in-flight Claude stream by sending SIGTERM.

    Returns True if a process was found and terminated.
    The session is preserved — next message can --resume.
    """
    proc = _active_processes.get(user_key)
    if proc and proc.returncode is None:
        logger.info(f"Cancelling stream for {user_key} (pid={proc.pid})")
        proc.terminate()
        bridge_event("stream_cancelled", user_key=user_key)
        return True
    return False


def _get_default_agent() -> str:
    """Read operator's configured agent name (default: chief)."""
    try:
        import yaml
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            op = yaml.safe_load(op_file.read_text()) or {}
            return op.get("agent_name", "chief")
    except Exception:
        pass
    return "chief"


# ── Event types ──────────────────────────────────────────────


@dataclass
class TextDelta:
    """Incremental text token from Claude."""
    text: str


@dataclass
class TextComplete:
    """Full accumulated text response from one assistant turn."""
    text: str


@dataclass
class ToolStart:
    """Claude started using a tool."""
    tool_id: str
    name: str
    input_preview: str


@dataclass
class ToolResult:
    """Tool execution completed."""
    tool_id: str
    is_error: bool
    preview: str


@dataclass
class SessionInit:
    """Session initialized."""
    session_id: str
    model: str
    tools: list[str]


@dataclass
class RateLimit:
    """Rate limit status update."""
    status: str
    resets_at: int


@dataclass
class ApiRetry:
    """API is being retried."""
    attempt: int
    max_retries: int
    delay_ms: int
    error: str


@dataclass
class SessionResult:
    """Final result of a Claude session."""
    session_id: str
    text: str
    is_error: bool
    duration_ms: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    num_turns: int


StreamEvent = (
    TextDelta | TextComplete | ToolStart | ToolResult |
    SessionInit | RateLimit | ApiRetry | SessionResult
)


# ── Tool descriptions ───────────────────────────────────────


def _describe_tool(name: str, input_data: dict) -> str:
    """Human-readable tool description."""
    if name == "Read":
        path = input_data.get("file_path", "")
        return f"Reading {path.split('/')[-1]}..." if "/" in path else f"Reading {path}..."
    elif name == "Bash":
        desc = input_data.get("description", "")
        cmd = input_data.get("command", "")
        return f"Running: {desc}" if desc else f"Running: {cmd[:60]}..."
    elif name in ("Grep", "Glob"):
        return f"Searching: {input_data.get('pattern', '')[:60]}..."
    elif name == "Edit":
        path = input_data.get("file_path", "")
        return f"Editing {path.split('/')[-1]}..." if "/" in path else f"Editing {path}..."
    elif name == "Write":
        path = input_data.get("file_path", "")
        return f"Writing {path.split('/')[-1]}..." if "/" in path else f"Writing {path}..."
    elif name == "Agent":
        return f"Delegating: {input_data.get('description', 'subagent')}..."
    elif name.startswith("mcp__"):
        parts = name.split("__")
        readable = parts[-1].replace("_", " ") if len(parts) > 1 else name
        return f"Using {readable}..."
    else:
        return f"Using {name}..."


# ── Event parser ─────────────────────────────────────────────


def parse_event(raw: str) -> StreamEvent | None:
    """Parse a single NDJSON line from claude stream-json output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse stream line: {raw[:200]}")
        return None

    event_type = data.get("type")

    if event_type == "system":
        subtype = data.get("subtype")
        if subtype == "init":
            return SessionInit(
                session_id=data.get("session_id", ""),
                model=data.get("model", ""),
                tools=data.get("tools", []),
            )
        elif subtype == "api_retry":
            return ApiRetry(
                attempt=data.get("attempt", 0),
                max_retries=data.get("max_retries", 5),
                delay_ms=data.get("retry_delay_ms", 0),
                error=data.get("error", "unknown"),
            )

    elif event_type == "stream_event":
        inner = data.get("event", {})
        inner_type = inner.get("type")

        if inner_type == "content_block_start":
            block = inner.get("content_block", {})
            if block.get("type") == "tool_use":
                return ToolStart(
                    tool_id=block.get("id", ""),
                    name=block.get("name", ""),
                    input_preview=_describe_tool(
                        block.get("name", ""), block.get("input", {})
                    ),
                )

        elif inner_type == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                return TextDelta(text=delta.get("text", ""))

    elif event_type == "assistant":
        msg = data.get("message", {})
        content = msg.get("content", [])
        text_parts = [b["text"] for b in content if b.get("type") == "text"]
        if text_parts:
            return TextComplete(text="".join(text_parts))

    elif event_type == "user":
        msg = data.get("message", {})
        content = msg.get("content", [])
        for block in content:
            if block.get("type") == "tool_result":
                preview = str(block.get("content", ""))[:200]
                return ToolResult(
                    tool_id=block.get("tool_use_id", ""),
                    is_error=block.get("is_error", False),
                    preview=preview,
                )

    elif event_type == "rate_limit_event":
        info = data.get("rate_limit_info", {})
        return RateLimit(
            status=info.get("status", "unknown"),
            resets_at=info.get("resetsAt", 0),
        )

    elif event_type == "result":
        usage = data.get("usage", {})
        return SessionResult(
            session_id=data.get("session_id", ""),
            text=data.get("result", ""),
            is_error=data.get("is_error", False),
            duration_ms=data.get("duration_ms", 0),
            cost_usd=data.get("total_cost_usd", 0.0),
            input_tokens=usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            num_turns=data.get("num_turns", 0),
        )

    return None


# ── Session persistence ──────────────────────────────────────


def _locked_json_read(path: Path) -> dict:
    """Read a JSON file with shared (read) lock."""
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {path.name}: {e}")
        return {}


def _locked_json_write(path: Path, data: dict):
    """Write a JSON file atomically with exclusive lock.

    Writes to a temp file first, then renames — so readers never see
    a partial write even without locking on the read side.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            fcntl.flock(f, fcntl.LOCK_UN)
        os.rename(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


SESSION_MAX_AGE = 30 * 60  # 30 min inactivity → fresh session (was 24h, caused bloated context)


def get_session_id(user_key: str) -> str | None:
    """Get stored session ID for a user key."""
    entry = _locked_json_read(SESSIONS_FILE).get(user_key)
    if entry is None:
        return None
    # Backward compat: old format stored bare session_id string
    if isinstance(entry, str):
        return entry
    # Check age
    if time.time() - entry.get("last_used", 0) > SESSION_MAX_AGE:
        clear_session(user_key)
        bridge_event("session_auto_expired", level="info",
                     user_key=user_key, age_h=24)
        return None
    return entry.get("session_id")


def save_session_id(user_key: str, session_id: str):
    """Persist session ID for a user key."""
    sessions = _locked_json_read(SESSIONS_FILE)
    sessions[user_key] = {
        "session_id": session_id,
        "last_used": time.time(),
    }
    # Prune stale entries while we're writing
    cutoff = time.time() - SESSION_MAX_AGE
    stale = [k for k, v in sessions.items()
             if isinstance(v, dict) and v.get("last_used", 0) < cutoff]
    for k in stale:
        sessions.pop(k)
        bridge_event("session_pruned", user_key=k)
    _locked_json_write(SESSIONS_FILE, sessions)
    bridge_event("session_saved", user_key=user_key, session_id=session_id[:12])


def clear_session(user_key: str):
    """Clear stored session for a user key."""
    sessions = _locked_json_read(SESSIONS_FILE)
    sessions.pop(user_key, None)
    _locked_json_write(SESSIONS_FILE, sessions)
    bridge_event("session_cleared", user_key=user_key)


# ── Agent dispatch ───────────────────────────────────────────


def _get_agent_names() -> list[str]:
    """Discover agent names from .md files."""
    return [p.stem for p in AGENTS_DIR.glob("*.md")]


def _build_agent_pattern() -> re.Pattern:
    names = _get_agent_names()
    if not names:
        return re.compile(r"(?!)")
    return re.compile(
        r"^(?:ask|tell|@)\s*("
        + "|".join(re.escape(n) for n in names)
        + r")\s+(?:to\s+)?(.+)",
        re.IGNORECASE | re.DOTALL,
    )


def detect_dispatch(message: str) -> tuple[str | None, str]:
    """Detect agent dispatch. Returns (agent_name, cleaned_message)."""
    pattern = _build_agent_pattern()
    match = pattern.match(message.strip())
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return None, message


# ── Auto-commit ──────────────────────────────────────────────


def _auto_commit():
    """Run auto-commit after bridge sessions (hooks don't fire for claude -p)."""
    try:
        subprocess.Popen(
            [str(WORKSPACE / "bin" / "auto-commit"), "bridge"],
            cwd=str(WORKSPACE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ── Core streaming function ─────────────────────────────────


async def stream_claude(
    message: str,
    user_key: str,
    cwd: str | None = None,
    image_paths: list[str] | None = None,
    max_turns: int = 100,
    max_budget_usd: float = 50.0,
) -> tuple[str | None, bool, AsyncGenerator[StreamEvent, None]]:
    """Spawn a claude process and stream typed events.

    Returns (agent_name, is_resumed, event_generator).
    The caller must fully consume the generator.
    """
    agent_name, clean_message = detect_dispatch(message)

    # Inject image read instructions
    if image_paths:
        img_lines = "\n".join(f"- Read the image file at: {p}" for p in image_paths)
        clean_message = (
            f"{clean_message or 'The user sent media without a caption.'}\n\n"
            f"[Attached media — use the Read tool to view these files]\n{img_lines}"
        )

    # Build command — lightweight Chief identity without full --agent harness.
    # --agent chief triggers CLAUDE.md chain + hooks + MCP on EVERY message
    # (each is a new process). This condensed prompt gives Chief's personality
    # without the startup overhead.
    cmd = [
        "claude",
        "-p",
        clean_message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
    ]

    # Chief identity — lightweight prompt for default conversations.
    # Reads operator name from config for personalization.
    if not agent_name:
        default_agent = _get_default_agent()
        _op_name = ""
        try:
            import yaml
            _op_file = Path.home() / ".aos" / "config" / "operator.yaml"
            if _op_file.exists():
                _op = yaml.safe_load(_op_file.read_text()) or {}
                _op_name = _op.get("name", "")
        except Exception:
            pass
        _greeting = f" You talk to the operator ({_op_name})" if _op_name else " You talk to the operator"
        cmd.extend([
            "--append-system-prompt",
            f"You are {default_agent.capitalize()}, the AOS orchestrator.{_greeting} "
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
        ])

    # Session resumption (not for agent dispatches — they get fresh context)
    session_id = None
    is_resumed = False
    if not agent_name:
        session_id = get_session_id(user_key)
        if session_id:
            cmd.extend(["--resume", session_id])
            is_resumed = True
            bridge_event("session_resuming", user_key=user_key,
                         session_id=session_id[:12])

    # Agent dispatch: "ask steward to ..." — use --agent for proper model/identity
    if agent_name:
        cmd.extend(["--agent", agent_name])

    work_dir = cwd or str(Path.home())

    async def _generate():
        nonlocal session_id, is_resumed
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                limit=16 * 1024 * 1024,  # 16MB buffer for image-heavy stream output
            )
        except FileNotFoundError:
            yield SessionResult(
                session_id="",
                text="Error: claude CLI not found.",
                is_error=True,
                duration_ms=0,
                cost_usd=0,
                input_tokens=0,
                output_tokens=0,
                num_turns=0,
            )
            return

        # Register process so stop button can kill it
        _active_processes[user_key] = proc

        new_session_id = None
        is_stale_session = False

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = parse_event(line)
                if event is None:
                    # Check for stale session error
                    if "Could not find session" in line or "session not found" in line.lower():
                        is_stale_session = True
                    continue

                # Track session ID
                if isinstance(event, SessionInit):
                    new_session_id = event.session_id
                elif isinstance(event, SessionResult):
                    new_session_id = event.session_id or new_session_id

                yield event

        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield SessionResult(
                session_id="",
                text=f"Stream error: {e}",
                is_error=True,
                duration_ms=0,
                cost_usd=0,
                input_tokens=0,
                output_tokens=0,
                num_turns=0,
            )

        # Wait for process to finish and unregister
        await proc.wait()
        _active_processes.pop(user_key, None)

        # Handle stale session: retry without --resume
        if is_stale_session and session_id:
            logger.warning(f"Stale session {session_id} for {user_key}, retrying fresh")
            bridge_event("session_stale", level="warn",
                         user_key=user_key, session_id=session_id[:12])
            clear_session(user_key)
            is_resumed = False  # override — we're starting fresh
            async for event in _retry_fresh(
                clean_message, user_key, agent_name, work_dir,
                max_turns, max_budget_usd,
            ):
                yield event
            return

        # Save new session ID
        if new_session_id and not agent_name:
            save_session_id(user_key, new_session_id)

        # Auto-commit
        _auto_commit()

    return agent_name, is_resumed, _generate()


async def _retry_fresh(
    message: str,
    user_key: str,
    agent_name: str | None,
    work_dir: str,
    max_turns: int,
    max_budget_usd: float,
):
    """Retry without session resumption."""
    cmd = [
        "claude",
        "-p",
        message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    # Load proper agent identity
    if agent_name:
        cmd.extend(["--agent", agent_name])
    else:
        cmd.extend(["--agent", _get_default_agent()])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
    except FileNotFoundError:
        yield SessionResult(
            session_id="",
            text="Error: claude CLI not found.",
            is_error=True,
            duration_ms=0,
            cost_usd=0,
            input_tokens=0,
            output_tokens=0,
            num_turns=0,
        )
        return

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        event = parse_event(line)
        if event is None:
            continue
        if isinstance(event, SessionResult) and event.session_id and not agent_name:
            save_session_id(user_key, event.session_id)
        yield event

    await proc.wait()
    _auto_commit()
