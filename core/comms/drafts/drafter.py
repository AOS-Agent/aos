"""Reply drafter.

Generates context-aware reply drafts using Claude Code CLI.
Never calls the Claude API directly — uses `claude -p` (print mode)
which goes through the Claude Code subscription.

The drafter:
1. Receives a DraftContext (assembled by context.py)
2. Builds a prompt that includes person context, style samples, edit history
3. Calls `claude -p` to generate the draft
4. Returns a DraftResult with the text + confidence

Confidence scoring:
- Base: 0.4 (we have the conversation but no style data)
- +0.2 if we have communication patterns
- +0.2 if we have >= 3 style samples
- +0.1 if we have edit history (learned from corrections)
- Result: 0.4 to 0.9 range

Usage:
    from drafts.context import assemble_context
    from drafts.drafter import draft_reply

    ctx = assemble_context(person_id, conv_id, "whatsapp", conn)
    result = draft_reply(ctx)
    print(result.text)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from .context import DraftContext

log = logging.getLogger(__name__)

# Claude Code CLI — find the binary
CLAUDE_BIN = shutil.which("claude") or "/usr/local/bin/claude"


@dataclass
class DraftResult:
    """Result of a draft attempt."""
    text: str
    confidence: float      # 0.0 to 1.0
    reasoning: str = ""    # Why this draft was composed this way
    warning: str = ""      # If something limited the draft quality
    person_id: str = ""
    person_name: str = ""


def _build_prompt(ctx: DraftContext) -> str:
    """Build the drafting prompt from context."""
    parts = []

    parts.append(
        "You are drafting a reply on behalf of an operator. "
        "Match their voice and style exactly — you are ghostwriting, not composing your own message. "
        "The reply should sound like THEM, not like an AI assistant."
    )
    parts.append("")

    # Person context
    parts.append(ctx.to_prompt_context())

    # The actual request
    parts.append(f"## Message to reply to")
    parts.append(f"{ctx.person_name}: {ctx.last_inbound}")
    parts.append("")

    # Instructions
    parts.append("## Instructions")
    parts.append("- Write ONLY the reply text. No explanations, no quotes, no preamble.")
    parts.append("- Match the operator's voice: length, tone, emoji usage, language.")
    parts.append("- If the conversation is in a mix of English and Urdu/Arabic, match that pattern.")
    parts.append("- Keep it natural and conversational — this is a real message to a real person.")

    if ctx.style_edits:
        parts.append("- IMPORTANT: Learn from the correction history above. Adjust your style accordingly.")

    if not ctx.has_style_samples:
        parts.append("- NOTE: No prior outbound messages available. Keep the reply brief and neutral.")

    return "\n".join(parts)


def _compute_confidence(ctx: DraftContext) -> float:
    """Compute confidence score based on available context."""
    score = 0.4  # Base: we have the conversation

    if ctx.has_patterns:
        score += 0.2  # We know their communication patterns

    if ctx.has_style_samples:
        score += 0.2  # We have voice samples

    if ctx.has_edit_history:
        score += 0.1  # We've learned from corrections

    return min(score, 0.9)


def draft_reply(ctx: DraftContext, timeout: int = 30) -> DraftResult:
    """Generate a draft reply using Claude Code CLI.

    Args:
        ctx: Assembled draft context
        timeout: Max seconds to wait for Claude response

    Returns:
        DraftResult with the draft text and confidence
    """
    result = DraftResult(
        text="",
        confidence=0.0,
        person_id=ctx.person_id,
        person_name=ctx.person_name,
    )

    # Check if we have enough to work with
    if not ctx.last_inbound:
        result.warning = "No inbound message to reply to"
        return result

    confidence = _compute_confidence(ctx)

    # Build prompt
    prompt = _build_prompt(ctx)

    # Call Claude Code CLI
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if proc.returncode != 0:
            result.warning = f"Claude CLI returned non-zero: {proc.stderr[:200]}"
            log.warning(f"Draft generation failed: {proc.stderr[:200]}")
            return result

        draft_text = proc.stdout.strip()

        if not draft_text:
            result.warning = "Claude returned empty response"
            return result

        result.text = draft_text
        result.confidence = confidence
        result.reasoning = (
            f"Drafted with {len(ctx.style_samples)} style samples, "
            f"{'patterns' if ctx.has_patterns else 'no patterns'}, "
            f"{'edit history' if ctx.has_edit_history else 'no edit history'}"
        )

        if not ctx.has_style_samples:
            result.warning = "No style samples — draft may not match operator's voice"

    except subprocess.TimeoutExpired:
        result.warning = f"Claude CLI timed out after {timeout}s"
        log.warning(f"Draft generation timed out for {ctx.person_name}")

    except FileNotFoundError:
        result.warning = "Claude CLI not found — install Claude Code"
        log.error("claude binary not found")

    except Exception as e:
        result.warning = f"Draft generation error: {str(e)[:200]}"
        log.error(f"Draft generation failed: {e}")

    return result
