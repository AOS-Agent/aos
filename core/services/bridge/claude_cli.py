"""Wrapper around `claude -p` for headless Claude Code interaction."""

import asyncio
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

import logging

from activity_client import log_activity, update_activity
from execution_logger import log_execution
from tracing import trace_claude_call

logger = logging.getLogger("bridge.claude_cli")


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
        pass  # Never block the bridge for a commit failure

WORKSPACE = Path.home() / "aos"
SESSIONS_FILE = WORKSPACE / "data" / "bridge" / "sessions.json"
AGENTS_DIR = WORKSPACE / ".claude" / "agents"


@dataclass
class StreamEvent:
    """Event yielded during Claude response streaming."""
    kind: str          # "text", "tool", "error"
    text: str          # accumulated text (for "text"), description (for "tool")


def _describe_tool(name: str, input_data: dict) -> str:
    """Create a short human-readable description of a tool use."""
    if name == "Read":
        path = input_data.get("file_path", "")
        short = path.split("/")[-1] if "/" in path else path
        return f"Reading {short}..."
    elif name == "Bash":
        cmd = input_data.get("command", "")
        desc = input_data.get("description", "")
        return f"Running: {desc}" if desc else f"Running: {cmd[:60]}..."
    elif name in ("Grep", "Glob"):
        pattern = input_data.get("pattern", "")
        return f"Searching: {pattern[:60]}..."
    elif name == "Edit":
        path = input_data.get("file_path", "")
        short = path.split("/")[-1] if "/" in path else path
        return f"Editing {short}..."
    elif name == "Write":
        path = input_data.get("file_path", "")
        short = path.split("/")[-1] if "/" in path else path
        return f"Writing {short}..."
    elif name == "Agent":
        desc = input_data.get("description", "")
        return f"Delegating: {desc}" if desc else "Delegating to agent..."
    elif name.startswith("mcp__"):
        # MCP tool — extract readable name
        parts = name.split("__")
        readable = parts[-1].replace("_", " ") if len(parts) > 1 else name
        return f"Using {readable}..."
    else:
        return f"Using {name}..."

def _get_agent_names() -> list[str]:
    """Discover agent names dynamically from .md files."""
    return [p.stem for p in AGENTS_DIR.glob("*.md")]


def _build_agent_pattern() -> re.Pattern:
    """Build dispatch regex from discovered agents."""
    names = _get_agent_names()
    if not names:
        return re.compile(r'(?!)')  # matches nothing
    return re.compile(
        r'^(?:ask|tell|@)\s*(' + '|'.join(re.escape(n) for n in names) + r')\s+(?:to\s+)?(.+)',
        re.IGNORECASE | re.DOTALL
    )


def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        return json.loads(SESSIONS_FILE.read_text())
    return {}


def _save_sessions(sessions: dict):
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))


def _detect_dispatch(message: str) -> tuple[str | None, str]:
    """Detect agent dispatch in message. Returns (agent_name, cleaned_message)."""
    pattern = _build_agent_pattern()
    match = pattern.match(message.strip())
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return None, message


def _get_agent_prompt(agent_name: str) -> str | None:
    """Read agent definition and strip YAML frontmatter."""
    path = AGENTS_DIR / f"{agent_name}.md"
    if not path.exists():
        return None
    text = path.read_text()
    # Strip YAML frontmatter (--- ... ---)
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].strip()
    return text


def _build_cmd(clean_message: str, agent_name: str | None, user_key: str,
                output_format: str = "json",
                image_paths: list[str] | None = None) -> tuple[list[str], str]:
    """Build the claude CLI command. Returns (cmd, log_agent).

    Args:
        image_paths: Optional list of local image file paths. Claude will be
            instructed to Read them so it can see the visual content.
    """
    sessions = _load_sessions()

    # If images are attached, weave them into the prompt
    if image_paths:
        img_instructions = "\n".join(
            f"- Read the image file at: {p}" for p in image_paths
        )
        clean_message = (
            f"{clean_message or 'The user sent media without a caption.'}\n\n"
            f"[Attached media — use the Read tool to view these files]\n"
            f"{img_instructions}"
        )

    # Read operator's agent name (default: chief)
    default_agent = "chief"
    try:
        import yaml
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            op = yaml.safe_load(op_file.read_text()) or {}
            default_agent = op.get("agent_name", "chief")
    except Exception:
        pass

    cmd = [
        "claude", "-p", clean_message,
        "--output-format", output_format,
        "--allowedTools", "*",
        "--dangerously-skip-permissions",
        "--chrome",
    ]

    if output_format == "stream-json":
        cmd.extend(["--verbose", "--include-partial-messages"])

    if agent_name:
        # Use --agent for proper agent loading (respects frontmatter: model, tools)
        cmd.extend(["--agent", agent_name])
        log_agent = agent_name
    else:
        session_id = sessions.get(user_key)
        if session_id:
            cmd.extend(["--resume", session_id])
        else:
            # Default to the operator's main agent
            cmd.extend(["--agent", default_agent])
        log_agent = default_agent

    return cmd, log_agent


def ask_claude(message: str, user_key: str,
               image_paths: list[str] | None = None,
               cwd: str | None = None) -> str:
    """Send a message to Claude Code and return the response.

    Supports agent dispatch: "ask ops to check health" routes to ops agent.

    Args:
        message: The user's message
        user_key: Unique key per user/channel (e.g. "telegram:123456")
        image_paths: Optional list of local image file paths to attach
        cwd: Working directory for Claude (default: WORKSPACE)
    """
    agent_name, clean_message = _detect_dispatch(message)
    cmd, log_agent = _build_cmd(clean_message, agent_name, user_key, "json",
                                image_paths=image_paths)

    aid = log_activity(log_agent, "invoke", status="running",
                       summary=clean_message[:100])
    start = time.time()

    work_dir = cwd or str(WORKSPACE)
    with trace_claude_call(log_agent, clean_message, user_key, cwd=work_dir) as span:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=work_dir,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            update_activity(aid, "failed", summary="Timed out (5 min)", duration_ms=duration_ms)
            span.set_error("Timed out (5 min)", duration_ms=duration_ms)
            log_execution(task=clean_message[:200], approach="claude-cli",
                          success=False, duration_ms=duration_ms,
                          error="timeout", agent=log_agent)
            return "Request timed out (5 min limit)."
        except FileNotFoundError:
            update_activity(aid, "failed", summary="claude CLI not found")
            span.set_error("claude CLI not found")
            log_execution(task=clean_message[:200], approach="claude-cli",
                          success=False, error="cli-not-found", agent=log_agent)
            return "Error: `claude` CLI not found. Is Claude Code installed?"

        duration_ms = int((time.time() - start) * 1000)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            error_msg = stderr[:200] if stderr else "Unknown error"
            update_activity(aid, "failed", summary=error_msg, duration_ms=duration_ms)
            span.set_error(error_msg, duration_ms=duration_ms)
            log_execution(task=clean_message[:200], approach="claude-cli",
                          success=False, duration_ms=duration_ms,
                          error=error_msg[:200], agent=log_agent)
            if stderr:
                return f"Error: {stderr[:500]}"
            return "Claude returned an error."

        try:
            data = json.loads(result.stdout)
            response = data.get("result", "")
            new_session_id = data.get("session_id")
            if new_session_id and not agent_name:
                sessions = _load_sessions()
                sessions[user_key] = new_session_id
                _save_sessions(sessions)
            update_activity(aid, "completed", summary=response[:200], duration_ms=duration_ms)
            span.set_result(response[:2000], duration_ms=duration_ms)
            log_execution(task=clean_message[:200], approach="claude-cli",
                          success=True, duration_ms=duration_ms,
                          agent=log_agent, session_id=new_session_id)
            _auto_commit()
            return response if response else "(empty response)"
        except json.JSONDecodeError:
            raw = result.stdout.strip()
            update_activity(aid, "completed", summary=raw[:200], duration_ms=duration_ms)
            span.set_result(raw[:2000], duration_ms=duration_ms)
            log_execution(task=clean_message[:200], approach="claude-cli",
                          success=True, duration_ms=duration_ms, agent=log_agent)
            _auto_commit()
            return raw or "(empty response)"


async def ask_claude_stream(message: str, user_key: str,
                            image_paths: list[str] | None = None,
                            cwd: str | None = None) -> AsyncGenerator[StreamEvent, None]:
    """Stream Claude's response as structured events.

    Yields StreamEvent objects:
    - kind="text": accumulated response text so far
    - kind="tool": description of a tool being used (e.g. "Reading file.py...")
    - kind="error": error message

    Also handles session saving.
    """
    agent_name, clean_message = _detect_dispatch(message)
    cmd, log_agent = _build_cmd(clean_message, agent_name, user_key, "stream-json",
                                image_paths=image_paths)

    aid = log_activity(log_agent, "invoke", status="running",
                       summary=clean_message[:100])
    start = time.time()
    work_dir = cwd or str(WORKSPACE)

    # Start a trace span for this entire streaming call
    from tracing import trace_claude_call, init_tracing
    _trace_ctx = trace_claude_call(log_agent, clean_message, user_key, cwd=work_dir)
    span = _trace_ctx.__enter__()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            limit=16 * 1024 * 1024,  # 16MB buffer for image-heavy stream output
        )
    except FileNotFoundError:
        update_activity(aid, "failed", summary="claude CLI not found")
        span.set_error("claude CLI not found")
        _trace_ctx.__exit__(None, None, None)
        yield StreamEvent("error", "claude CLI not found. Is Claude Code installed?")
        return

    accumulated = ""
    session_id = None
    final_result = None
    tools_seen = []

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "assistant":
                # Full assistant message — contains tool_use and text blocks
                content = data.get("message", {}).get("content", [])
                for block in content:
                    if block.get("type") == "tool_use":
                        desc = _describe_tool(block["name"], block.get("input", {}))
                        span.add_tool(block["name"])
                        tools_seen.append(block["name"])
                        yield StreamEvent("tool", desc)
                    elif block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            accumulated = text
                            yield StreamEvent("text", accumulated)

            elif msg_type == "result":
                session_id = data.get("session_id")
                final_result = data.get("result", "")

    except asyncio.TimeoutError:
        proc.kill()
        duration_ms = int((time.time() - start) * 1000)
        update_activity(aid, "failed", summary="Timed out (5 min)", duration_ms=duration_ms)
        span.set_error("Timed out (5 min)", duration_ms=duration_ms)
        _trace_ctx.__exit__(None, None, None)
        yield StreamEvent("error", "Request timed out (5 min limit).")
        return

    await proc.wait()
    duration_ms = int((time.time() - start) * 1000)

    if proc.returncode != 0:
        stderr_bytes = await proc.stderr.read()
        stderr = stderr_bytes.decode().strip()

        # Check if this is a stale session error — retry without --resume
        if "No conversation found with session ID" in stderr:
            logger.warning(f"Stale session for {user_key}, clearing and retrying")
            clear_session(user_key)
            # Rebuild command without --resume
            cmd2, _ = _build_cmd(clean_message, agent_name, user_key, "stream-json",
                                 image_paths=image_paths)
            proc2 = await asyncio.create_subprocess_exec(
                *cmd2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=work_dir, limit=16 * 1024 * 1024,
            )
            async for raw_line in proc2.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type")
                if msg_type == "assistant":
                    content = data.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "tool_use":
                            desc = _describe_tool(block["name"], block.get("input", {}))
                            span.add_tool(block["name"])
                            yield StreamEvent("tool", desc)
                        elif block.get("type") == "text":
                            text = block.get("text", "")
                            if text.strip():
                                accumulated = text
                                yield StreamEvent("text", accumulated)
                elif msg_type == "result":
                    session_id = data.get("session_id")
                    final_result = data.get("result", "")
            await proc2.wait()
            duration_ms = int((time.time() - start) * 1000)
        else:
            error_msg = stderr[:200] if stderr else "Unknown error"
            update_activity(aid, "failed", summary=error_msg, duration_ms=duration_ms)
            span.set_error(error_msg, duration_ms=duration_ms)
            _trace_ctx.__exit__(None, None, None)
            if not accumulated:
                yield StreamEvent("error", f"Error: {stderr[:500]}" if stderr else "Claude returned an error.")
            return

    # Save session
    if session_id and not agent_name:
        try:
            sessions = _load_sessions()
            sessions[user_key] = session_id
            _save_sessions(sessions)
        except Exception as e:
            logger.warning(f"Failed to save session for {user_key}: {e}")

    response = final_result or accumulated or "(empty response)"
    update_activity(aid, "completed", summary=response[:200], duration_ms=duration_ms)
    span.set_result(response[:2000], duration_ms=duration_ms, tools_used=tools_seen)
    log_execution(task=clean_message[:200], approach="claude-cli-stream",
                  success=True, duration_ms=duration_ms,
                  tools_used=tools_seen, agent=log_agent,
                  session_id=session_id)
    _trace_ctx.__exit__(None, None, None)
    _auto_commit()

    # Yield final complete response if it differs from accumulated
    if response != accumulated:
        yield StreamEvent("text", response)


def clear_session(user_key: str):
    """Clear session for a user (start fresh conversation)."""
    sessions = _load_sessions()
    sessions.pop(user_key, None)
    _save_sessions(sessions)
