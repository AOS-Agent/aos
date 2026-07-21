"""Minimal LLM interface for the intelligence loop engine.

Rides the Claude Code harness CLI directly — `claude --print --model <m>
--output-format json` — the same proven path the comms enricher uses at
~15K messages/night (core/engine/comms/enrich/extract.py). Deliberately
NOT built on core.engine.execution.router: that module was never
committed (only pycache bytecode survives, see inbox i29) and every lazy
import of it fails at call time.

Subscription-only by design (no API keys — operator rule). One stable
call: `await complete(prompt)`. No JSON parsing of the model's answer,
no retries — callers that need structured output parse the returned
text themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess

logger = logging.getLogger(__name__)

# Logical model names the harness accepts directly ("haiku", "sonnet", ...).
DEFAULT_MODEL = os.environ.get("AOS_LOOP_MODEL", "haiku")

DEFAULT_TIMEOUT_S = 120


class LLMError(Exception):
    """Raised when an LLM call fails in a way the loop engine can't recover from."""


def _run_cli(prompt: str, model: str, system: str | None, timeout_s: int) -> str:
    cmd = ["claude", "--print", "--model", model, "--output-format", "json"]
    if system:
        cmd += ["--system-prompt", system]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group → killpg-able on timeout
    )
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise LLMError(f"claude CLI timeout after {timeout_s}s") from None

    if proc.returncode != 0:
        raise LLMError(f"claude CLI rc={proc.returncode}: {(err or out or '')[:300]}")
    try:
        envelope = json.loads(out)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMError(f"claude CLI returned non-JSON envelope: {(out or '')[:300]}") from exc
    result = envelope.get("result")
    if not isinstance(result, str):
        raise LLMError(f"claude CLI envelope missing result: {(out or '')[:300]}")
    return result


async def complete(
    prompt: str,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Run a single completion through the Claude Code harness (subscription).

    Returns the response text. Raises LLMError on timeout, non-zero exit,
    or a malformed CLI envelope.
    """
    return await asyncio.to_thread(_run_cli, prompt, model, system, timeout_s)
