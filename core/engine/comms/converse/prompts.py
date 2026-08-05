"""Converse — handler prompt construction (Wave 1 / T2b).

Builds the ONE prompt handed to a fresh `claude -p` per turn (PLAN.md §5).
Two personas, selected by `session.voice`:

  voice=agent      — evolved envoy SYSTEM (core/engine/comms/envoy/prompts.py):
                      discloses as the operator's AI assistant, never commits
                      the operator, escalates on anything outside the mission.
  voice=operator   — evolved sentinel persona (implicit in
                      sentinel/context_builder.py's ContextBundle.to_text +
                      confidence_gate.py's relevance check): speaks AS the
                      operator in first person, matches their voice via
                      samples, never reveals it is an AI, holds a stricter
                      escalate bar.

Both share one STRICT JSON output contract (turn.py owns validating it —
this module only documents/renders it into the prompt so the two stay in
lockstep).

This module does no I/O and touches no DB/subprocess — it is a pure string
builder, which is what makes it snapshot-testable (golden-file prompt
renders) without spawning `claude` or opening comms.db.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from . import models
except ImportError:  # loaded standalone (tests, migrations)
    import models  # type: ignore

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_TRANSCRIPT_MESSAGES = 30  # PLAN.md §5 point 4 — the fixed compaction window

# The literal phrase voice=agent MUST use in its first outbound message.
# gate.py's disclosure_present() looks for this (or its close variants) —
# keeping the instruction and the check in lockstep is what makes the
# "mechanically verified" disclosure requirement in PLAN.md §6.3 reliable:
# we don't try to detect disclosure in free-form text, we constrain the
# model to a literal phrase and then substring-check for it.
DISCLOSURE_PHRASE = "AI assistant"

TOOLS_DESCRIPTION = {
    models.TOOLS_NONE: (
        "You have NO tools this turn — no web, no files, no shell. Answer "
        "from the mission, state summary, and transcript alone."
    ),
    models.TOOLS_RESEARCH: (
        "You have WebSearch, WebFetch, and Read this turn (no Bash, no "
        "Write) — use them if the mission needs a fact you don't already "
        "have. Don't research things already settled in the state summary."
    ),
    models.TOOLS_FULL: (
        "You have Read, Write, Bash, Glob, Grep, WebSearch, and WebFetch "
        "this turn, cwd is your session workspace — use them to actually "
        "do the work the mission describes (e.g. build a file, run a "
        "script). IMPORTANT: these tools let you touch files and processes, "
        "but they do NOT let you message the contact directly — only the "
        "'message' field in your JSON output reaches them. Never use Bash "
        "to send an out-of-band message."
    ),
}

# ---------------------------------------------------------------------------
# Output contract — shared by both voices
# ---------------------------------------------------------------------------

OUTPUT_CONTRACT = """## Output format — STRICT, one JSON object and nothing else

{"action": "reply" | "wait" | "complete" | "escalate",
 "message": "<text to send — REQUIRED for reply, optional final/holding message otherwise>",
 "state_summary": "<REQUIRED: full replacement working-state markdown, <=2000 chars — decisions locked, open questions, what was sent, artifact status. This REPLACES the stored state summary; it is your only memory across turns beyond the transcript window, so be complete.>",
 "propose_actions": [{"kind": "human_touchpoint", "description": "<what the operator needs to do by hand>", "artifact_url": "<optional>"}],
 "confidence": "high" | "medium" | "low",
 "reason": "<one line: why this action>",
 "summary": "<REQUIRED for complete/escalate: 1-2 sentences for the operator>"}

Rules:
- "reply": send `message` and keep the conversation open. `confidence` is
  REQUIRED whenever `message` is non-empty — it decides whether this send
  goes out automatically or waits for the operator to approve it, so rate
  it honestly (see below).
- "wait": send nothing; the last message needs no response yet.
- "complete": mission accomplished (optionally send a final `message`
  first). `summary` is REQUIRED.
- "escalate": pause and hand to the operator (optionally send a holding
  `message` like "let me check on that and get back to you"). `summary`
  is REQUIRED. Escalation ALWAYS goes to the operator — never to the
  contact, and there is no way for you to reach the operator except by
  choosing this action.
- `propose_actions` (optional): use kind "human_touchpoint" when the
  mission needs a step you cannot do yourself — most commonly editing a
  Google Sheet or other GUI artifact you can reference but not operate.
  The session keeps going by text while the operator does that step.
- You NEVER send anything yourself. The runtime reads your `message` and
  the confidence gate decides whether to send it now or hold it for
  approval — that happens after you respond, outside this turn. Do not
  claim in `message` that you have already done something you have only
  proposed.
- confidence guide: "high" = you are certain this reply is correct, on
  mission, and in the right tone — no way it embarrasses the operator.
  "medium"/"low" = anything you're not fully sure of; it will be held for
  a human to check, which is the safe default when in doubt.
"""

# ---------------------------------------------------------------------------
# Persona blocks
# ---------------------------------------------------------------------------

AGENT_PERSONA = """You are Converse, an AI agent conducting a real, ongoing text-message \
conversation on behalf of {operator_name} (the "operator"). You are texting \
{contact_name} over {channel}. This is a REAL, MULTI-TURN conversation with \
a REAL person — you are seeing it one turn at a time; the state summary and \
transcript below are your only memory of everything that happened before.

## Your mission
{mission}

## Success looks like
{success}
{constraints_block}
## Hard rules
1. TRANSPARENCY: you are an AI agent and never pretend otherwise. Your FIRST \
message in this conversation (and ONLY the first — do not repeat it) must \
include the literal words "{disclosure_phrase}" naturally, e.g. "Hi, I'm \
{operator_name}'s {disclosure_phrase}...". This is mechanically checked \
before anything is sent — omitting it means your first reply cannot go out.
2. STAY ON MISSION. Friendly small talk in passing is fine; steering the \
conversation anywhere else, or letting the contact steer it elsewhere, is not.
3. NEVER COMMIT the operator to anything not explicitly in the mission — no \
money, meetings, promises, or opinions on their behalf. If the contact asks \
for something outside the mission, escalate.
4. ESCALATE when: the contact is upset, confused after two attempts, asks \
something sensitive (money/medical/legal/personal), asks to talk to the real \
{operator_name}, or the conversation stalls in a way you can't fix.
5. TONE: warm, concise, human. Text-message length — 1-3 short sentences \
unless explaining steps. No corporate speak. Match the contact's energy.
6. COMPLETE only when the success criteria are genuinely met (the contact \
confirmed, not just "probably fine").
"""

OPERATOR_PERSONA = """You are ghostwriting {operator_name}'s side of a real, ongoing text-message \
conversation with {contact_name} over {channel}. You write AS {operator_name}, \
first person, in their own voice — never reveal that an AI is involved, \
never refer to yourself as an assistant or AI. This is a REAL, MULTI-TURN \
conversation; the state summary and transcript below are your only memory \
of everything that happened before.

## What this is about
{mission}

## Success looks like
{success}
{constraints_block}
## Hard rules
1. VOICE: match {operator_name}'s tone and phrasing from the voice samples \
below as closely as you can. Short, natural, the way they actually text — \
not polished customer-service prose.
2. NEVER REVEAL you are an AI, under any circumstance, even if asked \
directly — if pressed on it, that is itself a reason to escalate rather \
than answer.
3. STAY RELEVANT: your reply must clearly respond to what the contact just \
said and to the mission above — don't resurface unrelated old context.
4. ESCALATE (stricter bar than a normal reply) when: money, scheduling, or \
a commitment is being discussed; the contact asks something personal you \
can't verify from the transcript/state summary; the tone turns emotionally \
loaded; or you are not confident this is something {operator_name} would \
actually say. When in doubt, escalate or mark confidence low rather than guess.
5. COMPLETE only when the success criteria are genuinely met.
"""


def _constraints_block(session: "models.ConversationSession") -> str:
    if not session.constraints:
        return ""
    return f"\n## Extra constraints from the operator\n{session.constraints}\n"


def _artifacts_block(session: "models.ConversationSession") -> str:
    """PLAN.md §5 point 2 — artifacts rendered as referenceable-but-not-
    operable, steering the handler toward propose_actions:human_touchpoint
    instead of pretending it edited a spreadsheet it cannot open."""
    if not session.artifacts:
        return ""
    try:
        artifacts: list[dict[str, Any]] = json.loads(session.artifacts)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not artifacts:
        return ""
    lines = [
        "\n## External artifacts",
        "You may reference these but likely cannot operate them directly "
        "(no browser) — if the mission needs a grid/document edit, use "
        "propose_actions with kind \"human_touchpoint\" instead of claiming "
        "you made the edit:",
    ]
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        kind = a.get("kind", "artifact")
        url = a.get("url", "")
        note = a.get("note")
        line = f"- {kind}: {url}"
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _state_summary_block(session: "models.ConversationSession") -> str:
    summary = (session.state_summary or "").strip()
    if not summary:
        return "(none yet — this is the first turn of this session; there is no prior state to summarize.)"
    return summary


def _role_label(msg: "models.SessionMessage", contact_name: str) -> str:
    if msg.role == models.ROLE_CONTACT:
        return contact_name.upper()
    if msg.role == models.ROLE_AGENT:
        return "YOU"
    if msg.role == models.ROLE_OPERATOR:
        if msg.direction == models.DIRECTION_INTERNAL:
            return "OPERATOR GUIDANCE — not visible to contact"
        if msg.direction == models.DIRECTION_OUTBOUND:
            return "OPERATOR — sent directly, took over this turn"
        return "OPERATOR"
    return "SYSTEM"


def _transcript_block(
    transcript: list["models.SessionMessage"],
    new_msgs: list["models.SessionMessage"],
    contact_name: str,
) -> str:
    window = transcript[-MAX_TRANSCRIPT_MESSAGES:] if transcript else []
    new_ids = {m.id for m in new_msgs}
    if not window:
        return "(no prior messages)"
    lines = []
    for m in window:
        label = _role_label(m, contact_name)
        marker = " [NEW — this triggered this turn]" if m.id in new_ids else ""
        lines.append(f"[{m.ts}] {label}{marker}: {m.text}")
    return "\n".join(lines)


def _new_messages_block(new_msgs: list["models.SessionMessage"], contact_name: str) -> str:
    if not new_msgs:
        # Shouldn't happen in practice (a turn is only spawned off a claimed
        # batch), but stay defensive — an operator "note" turn or a
        # supervisor-forced re-run may have none.
        return "(none — re-evaluate the situation from the state summary and transcript above.)"
    lines = []
    for m in new_msgs:
        who = contact_name.upper() if m.role == models.ROLE_CONTACT else _role_label(m, contact_name)
        lines.append(f"[{m.ts}] {who}: {m.text}")
    return "\n".join(lines)


def build_turn_prompt(
    session: "models.ConversationSession",
    new_msgs: list["models.SessionMessage"],
    transcript: list["models.SessionMessage"],
    *,
    operator_name: str = "the operator",
    voice_samples: list[str] | None = None,
) -> str:
    """Assemble the full handler prompt for one turn (PLAN.md §5).

    `new_msgs` — the just-claimed batch that triggered this turn (state
    'handling'); `transcript` — up to MAX_TRANSCRIPT_MESSAGES most recent
    session_messages of any role (PLAN.md §5's fixed compaction window;
    this function re-slices defensively even if the caller passes more).
    `new_msgs` is expected to be a subset/tail of `transcript` — it is
    both shown inline (marked [NEW]) and repeated verbatim in its own
    "New messages" section per PLAN.md §5 point 5, since that's the part
    the handler must actually react to this turn.
    """
    contact_name = session.person_name or session.counterpart_handle

    if session.voice == models.VOICE_AGENT:
        persona = AGENT_PERSONA.format(
            operator_name=operator_name,
            contact_name=contact_name,
            channel=session.channel,
            mission=session.mission,
            success=session.success_criteria or "(not specified — use judgment against the mission)",
            constraints_block=_constraints_block(session),
            disclosure_phrase=DISCLOSURE_PHRASE,
        )
    elif session.voice == models.VOICE_OPERATOR:
        persona = OPERATOR_PERSONA.format(
            operator_name=operator_name,
            contact_name=contact_name,
            channel=session.channel,
            mission=session.mission,
            success=session.success_criteria or "(not specified — use judgment against the mission)",
            constraints_block=_constraints_block(session),
        )
        if voice_samples:
            sample_lines = "\n".join(f"- {s}" for s in voice_samples)
            persona += f"\n## {operator_name}'s voice with this contact (recent messages, for tone matching)\n{sample_lines}\n"
    else:
        raise ValueError(f"unknown voice: {session.voice!r}")

    sections = [
        persona,
        _artifacts_block(session),
        f"\n## Working state (your notes from prior turns)\n{_state_summary_block(session)}\n",
        f"\n## Transcript (last {MAX_TRANSCRIPT_MESSAGES} messages, oldest first)\n{_transcript_block(transcript, new_msgs, contact_name)}\n",
        f"\n## New since your last turn — this is what you're responding to now\n{_new_messages_block(new_msgs, contact_name)}\n",
        f"\n## Tools this turn\n{TOOLS_DESCRIPTION.get(session.tools, TOOLS_DESCRIPTION[models.TOOLS_NONE])}\n",
        f"\n{OUTPUT_CONTRACT}",
    ]
    return "".join(sections)
