"""Execution logger — records task execution details for pattern compilation.

Appends JSONL entries to ~/.aos/logs/execution/YYYY-MM-DD.jsonl.
The pattern compiler (bin/compile-patterns) scans these daily to find
repeated tasks that can be compiled into deterministic scripts.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bridge.execution_log")

EXECUTION_LOG_DIR = Path.home() / ".aos" / "logs" / "execution"


def log_execution(
    task: str,
    approach: str,
    success: bool,
    tokens_est: int = 0,
    fallbacks_tried: int = 0,
    tools_used: list[str] | None = None,
    duration_ms: int = 0,
    error: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    commands: list[str] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    num_turns: int = 0,
    user_key: str | None = None,
):
    """Append an execution log entry for pattern compilation and analysis.

    Args:
        task: Natural language description of the task
        approach: Method used (e.g., "bash", "steer-ocr", "chrome-mcp", "api")
        success: Whether the task completed successfully
        tokens_est: Estimated tokens consumed
        fallbacks_tried: Number of fallback approaches attempted
        tools_used: List of tool names invoked
        duration_ms: Total execution time in milliseconds
        error: Error message if failed
        agent: Agent name if dispatched
        session_id: Claude session ID
        commands: Shell commands executed (for pattern compilation)
        input_tokens: Actual input tokens from SessionResult
        output_tokens: Actual output tokens from SessionResult
        cost_usd: Cost in USD from SessionResult
        num_turns: Number of agent turns from SessionResult
        user_key: Who triggered this (e.g., "telegram:123456")
    """
    EXECUTION_LOG_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = EXECUTION_LOG_DIR / f"{today}.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task": task[:200],
        "approach": approach,
        "success": success,
        "tokens_est": tokens_est,
        "fallbacks_tried": fallbacks_tried,
    }

    # Optional fields — only include if set
    if tools_used:
        entry["tools_used"] = tools_used
    if duration_ms:
        entry["duration_ms"] = duration_ms
    if error:
        entry["error"] = error[:500]
    if agent:
        entry["agent"] = agent
    if session_id:
        entry["session_id"] = session_id[:16]
    if commands:
        entry["commands"] = commands[:10]  # Cap at 10 commands
    if input_tokens:
        entry["input_tokens"] = input_tokens
    if output_tokens:
        entry["output_tokens"] = output_tokens
    if cost_usd:
        entry["cost_usd"] = round(cost_usd, 6)
    if num_turns:
        entry["num_turns"] = num_turns
    if user_key:
        entry["user_key"] = user_key

    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Failed to write execution log: %s", e)
