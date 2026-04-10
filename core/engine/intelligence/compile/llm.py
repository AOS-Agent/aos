"""LLM interface for the compilation engine.

Thin wrapper over core.engine.execution.router.ExecutionRouter, which
already handles:
    - Claude Code subscription (harness, default)
    - OpenRouter gateway
    - Direct Anthropic API
    - Local Ollama
    - Keychain credential lookup
    - Execution telemetry + EventBus emission

We don't reimplement any of that. This file exists to give the compile
engine a small, stable surface: `await complete(prompt, model="haiku")`
and a `LLMResponse` dataclass with the fields compile cares about.

Provider selection:
    - Default: "haiku" → routes through claude-code harness (subscription)
    - Override via model prefix: "openrouter/anthropic/claude-haiku-4.5"
    - Override via env var: AOS_COMPILE_MODEL=openrouter/anthropic/claude-haiku-4.5

JSON response handling:
    - When response_format='json', we ask the model to return strict JSON
      via the system prompt, then parse it. On parse failure, we retry
      once with a reformatting instruction.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Default model reference used when callers don't specify. "haiku" is a
# logical name — the execution router maps it via providers.yaml.
DEFAULT_MODEL = os.environ.get("AOS_COMPILE_MODEL", "haiku")

# Hard safety cap — compile responses should be small structured JSON.
DEFAULT_MAX_TOKENS = 2000


class LLMError(Exception):
    """Raised when an LLM call fails in a way compile can't recover from."""


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    raw_json: Any | None = None  # populated when response_format='json'


async def complete(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    response_format: str = "text",  # or "json"
    timeout: float = 120.0,
) -> LLMResponse:
    """Run a single completion through the AOS execution router.

    Args:
        prompt:          The user prompt.
        model:           Model reference (e.g. "haiku", "sonnet",
                         "openrouter/anthropic/claude-haiku-4.5").
        system_prompt:   Optional system prompt.
        max_tokens:      Max response tokens.
        response_format: "text" or "json". When "json", the system prompt
                         is augmented with strict-JSON instructions and
                         the response is parsed into raw_json.
        timeout:         Per-call timeout in seconds.

    Returns:
        LLMResponse with text, metadata, and optionally raw_json.

    Raises:
        LLMError: on non-OK status or JSON parse failure after retry.
    """
    # Lazy import — avoids pulling execution_router into every consumer.
    # Try both import styles so this works whether the caller runs from
    # repo root (core.engine.X) or from core/ cwd (engine.X).
    try:
        from engine.execution.router import ExecutionRouter
    except ImportError:
        from core.engine.execution.router import ExecutionRouter

    router = ExecutionRouter()

    # Augment system prompt for JSON mode
    sys_prompt = system_prompt or ""
    if response_format == "json":
        json_instruction = (
            "\n\nYou MUST respond with a single JSON object and nothing else. "
            "No prose before or after, no markdown code fence, no explanation. "
            "If you need to include prose, put it inside a string field."
        )
        sys_prompt = (sys_prompt + json_instruction).strip()

    result = await router.execute(
        prompt=prompt,
        model=model,
        system_prompt=sys_prompt or None,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    if result.status != "ok":
        raise LLMError(
            f"LLM call failed: provider={result.provider} "
            f"model={result.model} status={result.status} error={result.error}"
        )

    response = LLMResponse(
        text=result.text,
        model=result.model,
        provider=result.provider,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
    )

    if response_format == "json":
        response.raw_json = _parse_json_response(result.text)
        if response.raw_json is None:
            # One retry with a reformatting nudge
            retry = await router.execute(
                prompt=(
                    "Your previous response was not valid JSON. Respond "
                    "with a single JSON object matching the requested schema "
                    "and NOTHING else. Previous response:\n\n" + result.text
                ),
                model=model,
                system_prompt=sys_prompt or None,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if retry.status == "ok":
                parsed = _parse_json_response(retry.text)
                if parsed is not None:
                    response.text = retry.text
                    response.raw_json = parsed
                    response.tokens_in += retry.tokens_in
                    response.tokens_out += retry.tokens_out
                    response.duration_ms += retry.duration_ms
                    return response
            raise LLMError(
                f"LLM returned unparseable JSON after retry. "
                f"Last response head: {result.text[:200]!r}"
            )

    return response


def _parse_json_response(text: str) -> Any | None:
    """Parse an LLM text response as JSON, tolerating minor noise.

    Handles:
        - bare JSON object
        - JSON inside markdown code fence (```json ... ```)
        - JSON with leading/trailing whitespace or prose
    """
    if not text:
        return None

    stripped = text.strip()

    # Direct parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", stripped, re.S)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Grab the first { ... } block (greedy-balanced enough for Haiku output)
    brace_match = re.search(r"\{.*\}", stripped, re.S)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None
