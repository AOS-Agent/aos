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
        "You are ghostwriting a message as the operator. "
        "The recipient must not be able to tell this was AI-generated. "
        "Your #1 job is to sound EXACTLY like the operator — not like a polished AI. "
        "Study the exemplar messages and style rules below. They are ground truth."
    )
    parts.append("")

    # Person context
    parts.append(ctx.to_prompt_context())

    # The actual request
    parts.append("## Message to reply to")
    parts.append(f"{ctx.person_name}: {ctx.last_inbound}")
    parts.append("")

    # Instructions
    parts.append("## Instructions")
    parts.append("- Write ONLY the reply text. No explanations, no quotes, no preamble.")

    if ctx.has_style_intelligence:
        # Strong anti-AI-normalization instructions
        parts.append("- The STYLE RULES above are MANDATORY. Every single one.")
        parts.append("- The exemplar messages show EXACTLY how the operator writes. Match them.")
        parts.append(
            "- DO NOT 'improve' the operator's style. Do not add punctuation they don't use. "
            "Do not capitalize what they leave lowercase. Do not expand abbreviations they use. "
            "Do not normalize their laugh patterns. Do not add emojis they don't use."
        )
        parts.append(
            "- AI tells: periods at end of casual messages, perfect grammar in informal chats, "
            "'I hope this message finds you well', using 'haha' when they use 'ahahah'. "
            "These are detectable. Avoid them."
        )

        level = ctx.enhancement_level
        if level == "raw":
            parts.append(
                "- MODE: RAW — Replicate EVERYTHING exactly: typos, abbreviations, missing "
                "punctuation, fragments, weird spacing, extended letters. If they write "
                "'doingggg' you write 'doingggg'. If they skip periods, you skip periods. "
                "Zero corrections."
            )
        elif level == "elevated":
            parts.append(
                "- MODE: ELEVATED — Same voice, same personality, but sharper. Fix actual "
                "errors (not style choices), improve sentence structure where it helps clarity. "
                "Keep their abbreviations, emoji patterns, laugh style, and language mixing. "
                "Think: them on their best day, not a different person."
            )
        else:  # clean (default)
            parts.append(
                "- MODE: CLEAN — Fix only clear spelling mistakes (not abbreviations — 'ur' is "
                "intentional, not a typo). Fix only grammar that causes confusion. Keep everything "
                "else: no periods if they don't use them, no capitalization if they don't capitalize, "
                "same emoji patterns, same message length range."
            )
    else:
        # Fallback for no style intelligence
        parts.append("- Match the operator's voice: length, tone, emoji usage, language.")
        parts.append("- If the conversation is in a mix of English and Urdu/Arabic, match that pattern.")
        parts.append("- Keep it natural and conversational — this is a real message to a real person.")

    if ctx.style_edits:
        parts.append("- IMPORTANT: Learn from the correction history above. Adjust your style accordingly.")

    if not ctx.has_style_samples and not ctx.has_style_intelligence:
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

    if ctx.has_voice_profile:
        score += 0.15  # Operator voice DNA (primary)

    if ctx.has_style_intelligence:
        score += 0.15  # Per-relationship mode + exemplars (secondary)

    return min(score, 1.0)


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
            f"{'edit history' if ctx.has_edit_history else 'no edit history'}, "
            f"{'style: ' + ctx.active_mode + '/' + ctx.enhancement_level if ctx.has_style_intelligence else 'no style intelligence'}"
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
