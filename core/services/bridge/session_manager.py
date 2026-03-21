"""Session manager — spawns Claude Code processes and parses streaming events."""

import asyncio
import json
import logging
import re
import subprocess
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("bridge.session_manager")

WORKSPACE = Path.home() / "aos"
SESSIONS_FILE = WORKSPACE / "data" / "bridge" / "sessions.json"
AGENTS_DIR = WORKSPACE / ".claude" / "agents"


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


def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_sessions(sessions: dict):
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))


def get_session_id(user_key: str) -> str | None:
    """Get stored session ID for a user key."""
    return _load_sessions().get(user_key)


def save_session_id(user_key: str, session_id: str):
    """Persist session ID for a user key."""
    sessions = _load_sessions()
    sessions[user_key] = session_id
    _save_sessions(sessions)


def clear_session(user_key: str):
    """Clear stored session for a user key."""
    sessions = _load_sessions()
    sessions.pop(user_key, None)
    _save_sessions(sessions)


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
    max_turns: int = 25,
    max_budget_usd: float = 5.0,
) -> tuple[str | None, AsyncGenerator[StreamEvent, None]]:
    """Spawn a claude process and stream typed events.

    Returns (agent_name, event_generator).
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

    # Build command
    cmd = [
        "claude",
        "-p",
        clean_message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
    ]

    # Session resumption (not for agent dispatches — they get fresh context)
    session_id = None
    if not agent_name:
        session_id = get_session_id(user_key)
        if session_id:
            cmd.extend(["--resume", session_id])

    # Agent system prompt
    if agent_name:
        agent_path = AGENTS_DIR / f"{agent_name}.md"
        if agent_path.exists():
            cmd.extend(["--append-system-prompt-file", str(agent_path)])

    work_dir = cwd or str(WORKSPACE)

    async def _generate():
        nonlocal session_id
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

        # Wait for process to finish
        await proc.wait()

        # Handle stale session: retry without --resume
        if is_stale_session and session_id:
            logger.warning(f"Stale session {session_id} for {user_key}, retrying fresh")
            clear_session(user_key)
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

    return agent_name, _generate()


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
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    if agent_name:
        agent_path = AGENTS_DIR / f"{agent_name}.md"
        if agent_path.exists():
            cmd.extend(["--append-system-prompt-file", str(agent_path)])

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
