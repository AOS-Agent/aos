"""
Quick-assist endpoint — fast model call for page voice commands.

Receives voice/text input + current page context + available actions,
calls Claude Haiku to pick the right action, returns structured response.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assist"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ActionParamSpec(BaseModel):
    name: str
    type: str
    required: bool = True
    options: Optional[list[str]] = None


class ActionSpec(BaseModel):
    id: str
    label: str
    category: str = ""
    params: Optional[list[ActionParamSpec]] = None


class ContextSummary(BaseModel):
    focus: Optional[str] = None
    active_topics: list[str] = []
    recent_actions: list[str] = []


class AssistRequest(BaseModel):
    input: str
    page: str
    page_detail: Optional[str] = None
    actions: list[ActionSpec]
    context: Optional[ContextSummary] = None


class AssistResponse(BaseModel):
    action_id: Optional[str] = None
    params: dict = {}
    spoken: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# System prompt — kept tight for speed (~150 tokens)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a quick page assistant. Given a voice command and available actions, return the best match.
Return ONLY valid JSON: {"action_id": str|null, "params": {}, "spoken": str, "confidence": float}
Rules:
- Match the user's intent to exactly one action from the list.
- Fill params from what the user said. For enum params, pick the closest option.
- If no action matches, set action_id to null.
- "spoken" must be under 12 words — a brief confirmation.
- confidence: 0.0-1.0."""


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/api/assist", response_model=AssistResponse)
async def assist(request: Request, body: AssistRequest) -> AssistResponse:
    """Call fast model to resolve a voice command to a page action."""

    # Enrich context from backend store if not provided by frontend
    ctx = body.context
    if not ctx:
        context_store = getattr(request.app.state, "context_store", None)
        if context_store:
            try:
                store_ctx = context_store.get()
                ctx = ContextSummary(
                    focus=store_ctx.focus,
                    active_topics=store_ctx.active_topics[:3],
                    recent_actions=[
                        a.get("spoken", "") for a in store_ctx.recent_actions[-3:]
                    ],
                )
            except Exception:
                pass

    # Build compact action list for the prompt
    actions_compact = []
    for a in body.actions:
        entry: dict = {"id": a.id, "label": a.label}
        if a.params:
            entry["params"] = [
                {"name": p.name, "type": p.type}
                | ({"options": p.options} if p.options else {})
                for p in a.params
            ]
        actions_compact.append(entry)

    # Build user message
    page_str = body.page
    if body.page_detail:
        page_str += f" ({body.page_detail})"

    # Inject context lines if available
    context_lines = ""
    if ctx:
        parts = []
        if ctx.focus:
            parts.append(f"User focus: {ctx.focus}")
        if ctx.active_topics:
            parts.append(f"Topics: {', '.join(ctx.active_topics[:3])}")
        if ctx.recent_actions:
            parts.append(f"Recent: {'; '.join(ctx.recent_actions[-2:])}")
        if parts:
            context_lines = "\n".join(parts) + "\n"

    user_msg = f"{context_lines}Page: {page_str}\nCommand: {body.input}\nActions: {json.dumps(actions_compact, separators=(',', ':'))}"

    # Call Claude Haiku via CLI (same pattern as companion.py)
    prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "--print",
            "--model",
            "haiku",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=8
        )

        raw = stdout.decode().strip()

        # Extract JSON from response (model may wrap in markdown code block)
        if raw.startswith("```"):
            lines = raw.split("\n")
            json_lines = [l for l in lines if not l.startswith("```")]
            raw = "\n".join(json_lines).strip()

        result = json.loads(raw)
        response = AssistResponse(
            action_id=result.get("action_id"),
            params=result.get("params", {}),
            spoken=result.get("spoken", "Done."),
            confidence=result.get("confidence", 0.5),
        )

        # Log action to context store
        context_store = getattr(request.app.state, "context_store", None)
        if context_store and response.action_id:
            try:
                from datetime import datetime, timezone
                context_store.add_action({
                    "input": body.input,
                    "action_id": response.action_id,
                    "spoken": response.spoken,
                    "page": body.page,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass

        return response

    except asyncio.TimeoutError:
        logger.warning("Assist model call timed out")
        return AssistResponse(
            spoken="Sorry, that took too long. Try again.",
            confidence=0.0,
        )
    except json.JSONDecodeError as e:
        logger.warning("Assist model returned invalid JSON: %s", e)
        return AssistResponse(
            spoken="I didn't understand that. Try again?",
            confidence=0.0,
        )
    except FileNotFoundError:
        logger.error("claude CLI not found — is Claude Code installed?")
        return AssistResponse(
            spoken="Voice assistant not available right now.",
            confidence=0.0,
        )
    except Exception:
        logger.exception("Assist endpoint error")
        return AssistResponse(
            spoken="Something went wrong. Try again?",
            confidence=0.0,
        )
