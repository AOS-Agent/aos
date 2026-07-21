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


_claude_bin_cache: str | None = None


def _claude_bin() -> str:
    """Resolve the claude CLI robustly — background/cron shells carry a
    minimal PATH that lacks interactive shims (e.g. cmux's).

    Memoized per process: the binary can be briefly absent while an
    updater swaps it (observed live 2026-07-21 — /opt/homebrew/bin/claude
    became a fresh symlink mid-run), and os.path.exists returns False on
    transient OS errors. Resolve once, keep the answer."""
    global _claude_bin_cache
    if _claude_bin_cache:
        return _claude_bin_cache
    override = os.environ.get("AOS_CLAUDE_BIN")
    if override:
        _claude_bin_cache = override
        return override
    # Known real installs FIRST. shutil.which is last resort and must never
    # return an app-embedded shim (e.g. cmux's), which re-resolves "claude"
    # via PATH itself and dies headless with "claude not found in PATH".
    for candidate in (
        "/opt/homebrew/bin/claude",
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.bun/bin/claude"),
        "/usr/local/bin/claude",
    ):
        if os.path.exists(candidate):
            _claude_bin_cache = candidate
            return candidate
    import shutil

    found = shutil.which("claude")
    if found and ".app/" not in found:
        _claude_bin_cache = found
        return found
    raise LLMError("claude CLI not found (AOS_CLAUDE_BIN, known locations, PATH)")


def _run_cli(prompt: str, model: str, system: str | None, timeout_s: int) -> str:
    """Single CLI completion, resilient to the auto-updater window.

    Claude Code's self-updater rewrites the npm global install while other
    sessions run; the bin symlink blinks out for seconds at a time (observed
    twice on 2026-07-21). On spawn-time FileNotFoundError: drop the memoized
    path, back off, re-resolve, retry — up to 3 attempts."""
    global _claude_bin_cache
    import time

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return _run_cli_once(prompt, model, system, timeout_s)
        except (FileNotFoundError, LLMError) as exc:
            transient = isinstance(exc, FileNotFoundError) or "not found" in str(exc)
            if not transient or attempt == 2:
                raise
            last_exc = exc
            _claude_bin_cache = None
            time.sleep(10 * (attempt + 1))
    raise LLMError(f"claude CLI unavailable after retries: {last_exc}")


def _run_cli_once(prompt: str, model: str, system: str | None, timeout_s: int) -> str:
    claude = _claude_bin()
    # The claude launcher re-resolves itself via PATH — it fails with
    # "claude not found in PATH" under minimal cron/background shells even
    # when invoked by absolute path. Guarantee its own dir is on PATH.
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        dict.fromkeys(  # de-dupe, keep order
            [os.path.dirname(claude), "/opt/homebrew/bin", "/usr/local/bin"]
            + env.get("PATH", "").split(os.pathsep)
        )
    )
    cmd = [claude, "--print", "--model", model, "--output-format", "json"]
    if system:
        cmd += ["--system-prompt", system]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
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
