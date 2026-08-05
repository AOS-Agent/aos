"""Converse — the turn runner (Wave 1 / T2b).

Given a session row + its newly-claimed 'handling' inbound message(s)
(db.claim_batch), assembles the handler prompt (prompts.py), spawns ONE
fresh `claude -p` for the turn, and parses its STRICT JSON output
(PLAN.md §5). Pure input/output at the DB boundary: this module never
opens comms.db and never calls db.apply_turn_result — it hands back a
TurnOutcome for the caller (the supervisor, core/services/converse, T3)
to apply via db.py, after routing any outbound `message` through
converse/gate.py.

Fail-safe contract: run_turn() NEVER raises for a bad handler turn — a
timeout, non-zero exit, or malformed JSON all come back as
`TurnOutcome(ok=False, error=...)`. A caller that only acts when `ok` is
True gets the required safety property for free: malformed output means
the session's claimed messages stay exactly where claim_batch left them
('handling'), nothing is queued, nothing is sent — the supervisor's
crash-sweep/backoff (PLAN.md §4 step 6) is what puts them back to
'received' for retry, not this module.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from . import models, prompts
except ImportError:  # loaded standalone (tests, migrations)
    import models  # type: ignore
    import prompts  # type: ignore

HOME = Path.home()
WORKSPACE_ROOT = HOME / ".aos" / "work" / "converse"

# claude binary discovery — same search order as envoy/runner.py.
_CLAUDE_BIN = (
    shutil.which("claude")
    or next(
        (
            p
            for p in (
                "/opt/homebrew/bin/claude",
                str(HOME / ".claude" / "local" / "claude"),
                "/usr/local/bin/claude",
            )
            if Path(p).exists()
        ),
        "claude",
    )
)

MAX_STATE_SUMMARY_CHARS = 2000

# Invocation profile per session.tools (PLAN.md §5 "Invocation profiles").
# allowed_tools="" (tools=none) intentionally passes a literal empty string
# to --allowedTools, matching PLAN.md's table exactly — it revokes tool use
# rather than omitting the flag (which would fall back to defaults).
TOOL_PROFILES: dict[str, dict[str, Any]] = {
    models.TOOLS_NONE: {
        "allowed_tools": "",
        "timeout_s": 180,
        "extra_args": (),
        "needs_workspace": False,
    },
    models.TOOLS_RESEARCH: {
        "allowed_tools": "WebSearch,WebFetch,Read",
        "timeout_s": 420,
        "extra_args": (),
        "needs_workspace": False,
    },
    models.TOOLS_FULL: {
        "allowed_tools": "Read,Write,Bash,Glob,Grep,WebSearch,WebFetch",
        "timeout_s": 1200,
        "extra_args": ("--max-turns", "40"),
        "needs_workspace": True,
    },
}


class TurnParseError(ValueError):
    """Handler output didn't satisfy the strict JSON contract (PLAN.md §5)."""


@dataclass
class ParsedTurn:
    """One turn's validated handler output — safe to hand to db.py /
    gate.py once constructed; construction is where all the validation
    happens (_validate_contract), never after."""

    action: str
    message: str | None
    state_summary: str
    propose_actions: list[dict[str, Any]]
    confidence: str | None
    reason: str | None
    summary: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "message": self.message,
            "state_summary": self.state_summary,
            "propose_actions": self.propose_actions,
            "confidence": self.confidence,
            "reason": self.reason,
            "summary": self.summary,
        }


@dataclass
class TurnOutcome:
    """Result of run_turn() — always returned, never raised, for the
    caller to branch on `ok` without a try/except."""

    ok: bool
    parsed: ParsedTurn | None = None
    error: str | None = None
    raw_stdout: str | None = None
    raw_stderr: str | None = None
    returncode: int | None = None
    duration_s: float | None = None
    prompt: str | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# JSON extraction — envelope (--output-format json) then inner brace-match
# ---------------------------------------------------------------------------


def _extract_first_json_object(text: str) -> dict[str, Any]:
    """Find the first balanced top-level {...} object in `text`.

    Extends envoy/prompts.py's parse_action brace-matcher (PLAN.md §5:
    "the envoy parse_action brace-matcher (proven), extended for the new
    fields") with string-awareness: braces inside a JSON string literal
    (e.g. a `message` that itself contains "{" or "}") no longer throw off
    the depth count, which the original envoy matcher didn't handle.
    """
    if not text:
        raise TurnParseError("empty text — no JSON object found")
    start = text.find("{")
    if start == -1:
        raise TurnParseError("no '{' found in handler output")

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise TurnParseError(f"matched balanced braces but invalid JSON: {e}") from e
                return obj
    raise TurnParseError("no balanced '{...}' object found in handler output")


def _validate_contract(obj: Any) -> ParsedTurn:
    """Strict validation of the PLAN.md §5 output contract. Raises
    TurnParseError on any violation — see module docstring for why that's
    the safe default rather than best-effort coercion."""
    if not isinstance(obj, dict):
        raise TurnParseError(f"handler JSON must be an object, got {type(obj).__name__}")

    action = obj.get("action")
    if action not in models.TURN_ACTIONS:
        raise TurnParseError(f"invalid or missing 'action': {action!r} (must be one of {models.TURN_ACTIONS})")

    state_summary = obj.get("state_summary")
    if not isinstance(state_summary, str) or not state_summary.strip():
        raise TurnParseError("missing required non-empty 'state_summary'")
    if len(state_summary) > MAX_STATE_SUMMARY_CHARS:
        state_summary = state_summary[:MAX_STATE_SUMMARY_CHARS]

    message_raw = obj.get("message")
    if message_raw is not None and not isinstance(message_raw, str):
        raise TurnParseError(f"'message' must be a string if present, got {type(message_raw).__name__}")
    message = message_raw.strip() if isinstance(message_raw, str) else None
    if message == "":
        message = None

    if action == models.TURN_REPLY and not message:
        raise TurnParseError("action='reply' requires a non-empty 'message'")

    confidence = obj.get("confidence")
    if confidence is not None and confidence not in ("high", "medium", "low"):
        raise TurnParseError(f"invalid 'confidence': {confidence!r} (must be high/medium/low)")
    if message and confidence is None:
        raise TurnParseError("'confidence' is required whenever 'message' is present (the gate routes on it)")

    reason = obj.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise TurnParseError("'reason' must be a string if present")

    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise TurnParseError("'summary' must be a string if present")
    if action in (models.TURN_COMPLETE, models.TURN_ESCALATE) and not (summary and summary.strip()):
        raise TurnParseError(f"action={action!r} requires a non-empty 'summary' for the operator")

    propose_actions_raw = obj.get("propose_actions") or []
    if not isinstance(propose_actions_raw, list):
        raise TurnParseError("'propose_actions' must be a list if present")

    # send_reply is gate-owned (converse/gate.py proposes it for a held
    # reply) — the handler itself may only ask for kinds that represent
    # work it cannot do from inside a turn. See db.py apply_turn_result's
    # docstring for the same boundary stated from the DB side.
    allowed_kinds = {k for k in models.ACTION_KINDS if k != models.ACTION_SEND_REPLY}
    propose_actions: list[dict[str, Any]] = []
    for i, pa in enumerate(propose_actions_raw):
        if not isinstance(pa, dict):
            raise TurnParseError(f"propose_actions[{i}] is not an object")
        kind = pa.get("kind")
        if kind not in allowed_kinds:
            raise TurnParseError(
                f"propose_actions[{i}] has invalid kind {kind!r} "
                f"(handler may propose {sorted(allowed_kinds)})"
            )
        description = pa.get("description")
        if not isinstance(description, str) or not description.strip():
            raise TurnParseError(f"propose_actions[{i}] missing non-empty 'description'")
        propose_actions.append(dict(pa))

    return ParsedTurn(
        action=action,
        message=message,
        state_summary=state_summary,
        propose_actions=propose_actions,
        confidence=confidence,
        reason=reason,
        summary=summary,
    )


def parse_turn_output(raw: str) -> ParsedTurn:
    """Two-stage parse: the `claude --output-format json` envelope, then
    the handler's own JSON object embedded in its `result` string field
    (PLAN.md §5: "the action object is parsed from the result field").
    Raises TurnParseError on any failure — see run_turn for the fail-safe
    wrapper callers should actually use.
    """
    text = (raw or "").strip()
    if not text:
        raise TurnParseError("empty stdout from claude")

    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as e:
        raise TurnParseError(f"stdout is not valid JSON (--output-format json envelope expected): {e}") from e
    if not isinstance(envelope, dict):
        raise TurnParseError("--output-format json envelope is not a JSON object")
    if envelope.get("is_error"):
        raise TurnParseError(f"claude reported is_error: {envelope.get('result') or envelope.get('error')!r}")

    result_text = envelope.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        raise TurnParseError("envelope has no non-empty 'result' string field")

    obj = _extract_first_json_object(result_text)
    return _validate_contract(obj)


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def _session_workspace(session_id: str) -> Path:
    return WORKSPACE_ROOT / session_id


def build_command(session: "models.ConversationSession", claude_bin: str | None = None) -> list[str]:
    profile = TOOL_PROFILES.get(session.tools, TOOL_PROFILES[models.TOOLS_NONE])
    return [
        claude_bin or _CLAUDE_BIN,
        "--print",
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "--allowedTools",
        profile["allowed_tools"],
        "--output-format",
        "json",
        *profile["extra_args"],
    ]


def run_turn(
    session: "models.ConversationSession",
    new_msgs: list["models.SessionMessage"],
    transcript: list["models.SessionMessage"],
    *,
    operator_name: str = "the operator",
    voice_samples: list[str] | None = None,
    claude_bin: str | None = None,
    env: dict[str, str] | None = None,
) -> TurnOutcome:
    """Spawn one `claude -p` turn and return its parsed outcome.

    Never raises. Never touches comms.db. Never calls a channel's send().
    The caller is responsible for: claiming the batch first (db.claim_batch),
    routing `parsed.message` through converse/gate.py before any send, and
    applying the result via db.apply_turn_result (only when ok=True).
    """
    profile = TOOL_PROFILES.get(session.tools, TOOL_PROFILES[models.TOOLS_NONE])
    prompt = prompts.build_turn_prompt(
        session, new_msgs, transcript,
        operator_name=operator_name, voice_samples=voice_samples,
    )

    cwd: str | None = None
    if profile["needs_workspace"]:
        workspace = _session_workspace(session.id)
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return TurnOutcome(ok=False, error=f"could not create session workspace: {e}", prompt=prompt)
        cwd = str(workspace)

    cmd = build_command(session, claude_bin=claude_bin)
    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=profile["timeout_s"],
            cwd=cwd,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return TurnOutcome(
            ok=False,
            error=f"turn timed out after {profile['timeout_s']}s (tools={session.tools})",
            duration_s=time.monotonic() - start,
            prompt=prompt,
        )
    except Exception as e:  # noqa: BLE001 — subprocess launch failure must never raise into the caller
        return TurnOutcome(ok=False, error=f"failed to launch claude: {e}", prompt=prompt)

    duration = time.monotonic() - start

    if proc.returncode != 0:
        return TurnOutcome(
            ok=False,
            error=f"claude exited rc={proc.returncode}: {proc.stderr.strip()[:500]}",
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
            returncode=proc.returncode,
            duration_s=duration,
            prompt=prompt,
        )

    try:
        parsed = parse_turn_output(proc.stdout)
    except TurnParseError as e:
        return TurnOutcome(
            ok=False,
            error=f"unparseable turn output: {e}",
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
            returncode=proc.returncode,
            duration_s=duration,
            prompt=prompt,
        )

    return TurnOutcome(
        ok=True,
        parsed=parsed,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
        returncode=proc.returncode,
        duration_s=duration,
        prompt=prompt,
    )
