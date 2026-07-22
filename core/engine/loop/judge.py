"""Friction judge — LLM classifier for operator messages.

The sensor at the heart of the Intelligence Loop: given one user message
from a Claude Code session (plus a snippet of the assistant turn before
it), decide whether it is a REAL moment of operator friction — and first,
whether it is even a human typing at all.

This replaces the keyword matcher that flagged Envoy system prompts and
comms batch payloads as "frustration" (273/350 false positives in the
2026-07-20 weekly report). The council made its quality gate explicit:
this judge must pass the frozen eval set (loop-eval-v1) BEFORE it is
allowed to write a single signal row. Run the gate with:

    core/bin/cli/loop-eval run --judge-module core.engine.loop.judge:judge

Prompt discipline:
- Step 1 (machine_text) comes before classification — most historical
  false positives were machine text misread as human emotion.
- Few-shot examples are SYNTHETIC, never verbatim rows from the eval set
  (the gate must measure generalization, not memorization).
- Output is strict JSON; one malformed-output retry, then LLMError.
"""

from __future__ import annotations

import json
import os

from . import llm

MODEL = os.environ.get("AOS_LOOP_JUDGE_MODEL", "haiku")

SYSTEM_PROMPT = """You classify single messages from a human operator's terminal sessions with an AI agent. Your output feeds a self-improvement system, so precision matters more than recall: when genuinely torn, prefer "none".

Answer with STRICT JSON only, no prose: {"machine_text": <bool>, "label": "<correction|frustration|overreach|retry|none>"}

STEP 1 — machine_text. Is this text a HUMAN operator typing in the moment? Set machine_text=true (and label="none", always) if the text is generated or pasted content:
- agent/system prompts ("You are Envoy...", "You are an AI agent...")
- structured payloads: "Batch: N message(s), channel=...", message-ID lists
- XML-ish wrappers: <teammate-message>, <task-notification>, hook or command output
- pasted logs, error dumps, file paths alone, transcripts of earlier turns
Fragmented human notes with typos are NOT machine text — humans type messily.

STEP 2 — label (only if machine_text=false). Friction means the operator is pushing back on what the agent DID:
- "correction": the operator contradicts or faults a SPECIFIC prior agent action, output, or claim ("no that's not what I meant", "you missed X", "that's not the same font", "you're in the WRONG explorer", "your fix didn't work — it's still broken"). INCLUDES feedback on the agent's ongoing behavior ("your status updates need to be cleaner", "stop working in this session, use another").
  THE HARD BOUNDARY — all of these are "none", not correction:
  * design iteration and creative redirection, even when it voices dislike: "I don't like the visuals, let's think through the light/states", "the shapes are too basic, how do we make this 10x", "I'm not liking the direction — gain some grounding and I'll share my ideas"
  * brand-new instructions or preferences with no prior agent action faulted: "make the kaaba bigger", "get rid of the sun in the dial", "this should be mobile first"
  * asking the agent to double-check or be careful BEFORE any mistake is found: "make sure we're building this correctly", "I hope you looked deeply into X"
  * answering the agent's question with a different choice than it proposed
  The test: is a specific thing the agent already DID or CLAIMED being called wrong? If the message only shapes what happens NEXT, it is "none".
- "frustration": annoyance AT the agent's behavior or results — repeated failed fixes ("THE SHIFT IS STILL THERE"), emphatic caps about broken output ("MORE DOESNT OPEN A DROPDOWN"), impatience with the agent's pace ("what's taking so long"), "why did you / what are you doing". A bug report phrased as a neutral question with no emotional charge ("why is it when you click sign in it goes blank?") is "none" — frustration needs heat: repetition, caps, exasperation, or blame.
- "overreach": the operator calls out that the agent did MORE than asked or acted without approval.
- "retry": the operator asks to redo/rerun/revert because the attempt failed — INCLUDING failures of the surrounding system (API errors, stalled jobs), not just agent mistakes. EXCLUDES retries the operator attributes to their own environment ("sorry, my wifi was off — try again") — that is "none".
- "none": everything else — new instructions, questions, approvals ("ship it", including typos like "shit it"), preferences, design discussion, brainstorming, status checks, bug reports stated neutrally without blame or emphasis.

Calibration examples (synthetic):
- "You are Scout, an AI agent researching flights on behalf of..." -> {"machine_text": true, "label": "none"}
- "no no, I wanted the sidebar on the LEFT, you moved the whole panel" -> {"machine_text": false, "label": "correction"}
- "bro its STILL broken. third time. what are you even doing" -> {"machine_text": false, "label": "frustration"}
- "I only asked you to draft it, why did you send the email??" -> {"machine_text": false, "label": "overreach"}
- "hit another rate limit error, run it again" -> {"machine_text": false, "label": "retry"}
- "ok looks good, lets also add dark mode next" -> {"machine_text": false, "label": "none"}
- "hmm I dont love how the cards look... what would the 10x version be? feel free to get creative" -> {"machine_text": false, "label": "none"}
- "the header should be sticky and lets drop the shadows" -> {"machine_text": false, "label": "none"}
- "double-check the schema docs so we build this right" -> {"machine_text": false, "label": "none"}
- "why does the page go blank after login?" -> {"machine_text": false, "label": "none"}
- "Batch: 17 message(s), channel=whatsapp. Messages: [wa_1024] (inbound) hey..." -> {"machine_text": true, "label": "none"}"""

_VALID_LABELS = {"correction", "frustration", "overreach", "retry", "none"}


def _build_prompt(text: str, prev_snippet: str | None) -> str:
    parts = []
    if prev_snippet:
        parts.append(f"[end of the agent's previous message]\n...{prev_snippet}\n")
    parts.append(f"[operator message to classify]\n{text}")
    return "\n".join(parts)


def _parse(raw: str) -> dict | None:
    """Parse the model's JSON verdict; None if malformed."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        obj = json.loads(s[s.index("{"): s.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    label = obj.get("label")
    machine = obj.get("machine_text")
    if label not in _VALID_LABELS or not isinstance(machine, bool):
        return None
    if machine:
        label = "none"  # invariant: machine text is never friction
    return {"machine_text": machine, "label": label}


async def judge(text: str, prev_snippet: str | None = None) -> dict:
    """Classify one operator message. Gate-compatible signature.

    Returns {"machine_text": bool, "label": str}. Raises llm.LLMError if
    the model returns malformed output twice.
    """
    prompt = _build_prompt(text, prev_snippet)
    raw = await llm.complete(prompt, model=MODEL, system=SYSTEM_PROMPT)
    verdict = _parse(raw)
    if verdict is None:
        raw = await llm.complete(
            prompt + "\n\nReturn ONLY the JSON object, nothing else.",
            model=MODEL, system=SYSTEM_PROMPT,
        )
        verdict = _parse(raw)
    if verdict is None:
        raise llm.LLMError(f"judge returned unparseable verdict: {raw[:200]}")
    return verdict


def version_hash() -> str:
    """Stable hash of the judge's behavior-defining inputs (prompt + model).
    The eval gate records this on PASS; the nightly sensor refuses to run
    a judge whose hash has no matching pass marker."""
    import hashlib

    return hashlib.sha256((SYSTEM_PROMPT + "|" + MODEL).encode()).hexdigest()[:12]
