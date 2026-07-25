"""Friction judge — batched LLM classifier for operator messages.

The sensor at the heart of the Intelligence Loop: given user messages
from Claude Code sessions (plus a snippet of the assistant turn before
each), decide whether each is a REAL moment of operator friction — and
first, whether it is even a human typing at all.

v4 (10x refit, 2026-07-25): batch-first. One call classifies up to
BATCH_SIZE messages (research: 25–50 items/call costs <2pp accuracy on
haiku, >80% cheaper), behind a deterministic prefilter that discards
machine text and trivial approvals for free. The system prompt is
byte-stable across calls so Anthropic prompt caching hits when batches
fire back-to-back.

Council gate unchanged: this judge must pass the frozen eval set's TEST
split before writing a single signal row (loop-eval run --split test
--record). version_hash() covers prompt + model + batch format +
prefilter version — changing ANY of them re-locks the friction sensor.

Prompt discipline:
- machine_text decided before label — most historical false positives
  were machine text misread as human emotion. Enforced structurally
  after parsing too (machine_text=True forces label="none").
- Few-shot examples are SYNTHETIC, never verbatim eval rows.
- Output is strict JSON; retry once, then bisect the batch, then a
  single poison item degrades to {"label": "none", "judge_error": True}.
"""

from __future__ import annotations

import json
import os
import re

from . import llm

MODEL = os.environ.get("AOS_LOOP_JUDGE_MODEL", "haiku")

BATCH_SIZE = 30
BATCH_FORMAT = "batch-v1;size=30;item=xml;out=json-results"
PREFILTER_VERSION = "pf-v1"

_VALID_LABELS = {"correction", "defect_report", "frustration", "overreach", "retry", "none"}

# Head/tail truncation for long pastes — friction lives at the edges.
_HEAD_CHARS = 1200
_TAIL_CHARS = 300
_SNIPPET_CHARS = 300

SYSTEM_PROMPT = """You classify messages from a human operator's terminal sessions with an AI agent. Your output feeds a self-improvement system, so precision matters more than recall: when genuinely torn on an item, prefer "none".

You receive a numbered batch of items. Each <item id="N"> contains the operator message (<user>) and optionally the tail of the agent's preceding message (<prev_assistant>). Classify EVERY item independently — do not let one item's verdict bleed into the next.

For each item, decide two things IN ORDER:

STEP 1 — machine_text. Is this text a HUMAN operator typing in the moment? machine_text=true (and label MUST be "none") if the text is generated or pasted content:
- agent/system prompts ("You are Envoy...", "You are an AI agent...")
- structured payloads: "Batch: N message(s), channel=...", message-ID lists
- XML-ish wrappers: <teammate-message>, <task-notification>, hook or command output
- pasted logs, error dumps, bare file paths, JSON blobs, "[Image: ...]" markers, transcripts of earlier turns
Fragmented human notes with typos are NOT machine text — humans type messily.

STEP 2 — label (only meaningful if machine_text=false):
- "defect_report": the operator reports a flaw, bug, or quality shortfall in an ARTIFACT the agent built — UI defects ("the email isn't centered", "the glyphs are cut off", "arabic is left-aligned, should be right"), broken behavior ("zoom is broken", "clicking sign-in blanks the page", "where did the status line go?"), missing or wrong content in a produced document ("you missed the verses from X", "this should have been in the lesson"), or output quality shortfalls ("the rebuild didn't do a good job", "the extracted data seems off"). Needs NO emotional charge — a calm, even question-phrased report of something broken in agent work counts. Distinct from design preference: "I want it blue instead" is none; "it's rendering wrong / broken / missing" is defect_report.
- "correction": the operator contradicts or faults the agent's UNDERSTANDING, CLAIM, or interaction behavior ("no that's not what I meant", "you're in the WRONG explorer", "I don't think that's the new font, are you sure", "your status updates need to be cleaner", "the whole point was to do it in ANOTHER session").
- "frustration": annoyance AT the agent — repeated failed fixes ("THE SHIFT IS STILL THERE"), emphatic caps about broken output ("MORE DOESNT OPEN A DROPDOWN"), impatience with pace ("what's taking so long"), "why did you / what are you doing".
- "overreach": the operator calls out that the agent did MORE than asked or acted without approval.
- "retry": redo/rerun/revert because the attempt failed — INCLUDING surrounding-system failures (API errors, stalled jobs). EXCLUDES retries the operator attributes to their own environment ("sorry, my wifi was off — try again") — that is "none".
- "none": everything else — new instructions, preferences, design iteration and creative redirection even when it voices dislike ("I don't like the visuals, how do we make this 10x", "make the kaaba bigger"), asking the agent to double-check BEFORE any mistake is found, plain questions ("did you launch something on the iphone?"), plain requests ("can you put it on my phone"), approvals including typos ("ship it", "shit it"), status checks, brainstorming.

THE TEST for friction: is a specific thing the agent already DID, BUILT, or CLAIMED being called wrong? If the message only shapes what happens NEXT, it is "none".
PRECEDENCE when mixed: emotional heat (caps, repetition, exasperation) -> "frustration"; else artifact flaw -> "defect_report"; else misunderstanding/claim -> "correction".

Calibration examples (synthetic):
- "You are Scout, an AI agent researching flights on behalf of..." -> machine_text true, none
- "no no, I wanted the sidebar on the LEFT, you moved the whole panel" -> correction
- "the save button is cut off on the profile page" -> defect_report
- "why is the date showing in english numerals? it should be arabic" -> defect_report
- "chapter 3 is missing the last two sections you said were included" -> defect_report
- "bro its STILL broken. third time. what are you even doing" -> frustration
- "I only asked you to draft it, why did you send the email??" -> overreach
- "hit another rate limit error, run it again" -> retry
- "hmm I dont love how the cards look... what would the 10x version be? feel free to get creative" -> none
- "double-check the schema docs so we build this right" -> none
- "why does the page go blank after login?" -> defect_report
- "ok looks good, lets also add dark mode next" -> none
- "Batch: 17 message(s), channel=whatsapp. Messages: [wa_1024] (inbound) hey..." -> machine_text true, none

OUTPUT CONTRACT — respond with ONLY this JSON, no prose, no code fences:
{"results":[{"id":1,"machine_text":false,"label":"none"}, ...]}
Exactly one entry per item, ids exactly as given, in the given order. label must be one of: correction, defect_report, frustration, overreach, retry, none."""


# ── deterministic prefilter ─────────────────────────────────────────────────

_MACHINE_PATTERNS = (
    re.compile(r"^You are [A-Z][a-zA-Z]*,? (an?|the) (AI )?agent", re.IGNORECASE),
    re.compile(r"^Batch: \d+ message\(s\)"),
    re.compile(r"^<(teammate-message|task-notification|local-command|command-name|command-message|command-args|local-command-stdout|system-reminder)"),
    re.compile(r"^\[Image:"),
    re.compile(r"^This session is being continued"),
    re.compile(r"^\[Request interrupted"),
)
_TRIVIAL_RE = re.compile(
    r"^(ok(ay)?|yes+|yep|yeah|cool|nice|great|perfect|excellent|done|continue|cont|go|proceed|"
    r"sure|thanks?|ty|approved?|ship it|shit it|lgtm|k+|dobe|doen)[.!\s]*$",
    re.IGNORECASE,
)


def prefilter(text: str, prev_snippet: str | None = None) -> str | None:
    """Deterministic short-circuit. Returns "none" for items the judge
    never needs to see (machine text, trivial approvals/continuers, bare
    paths/JSON); None means "send to the judge". Pure function — any
    behavior change must bump PREFILTER_VERSION."""
    t = (text or "").strip()
    if not t:
        return "none"
    for pat in _MACHINE_PATTERNS:
        if pat.search(t):
            return "none"
    if len(t) <= 24 and _TRIVIAL_RE.match(t):
        return "none"
    # bare file path or URL, nothing else
    if len(t.split()) == 1 and ("/" in t or t.startswith("http")):
        return "none"
    # pure JSON blob
    if t.startswith("{") and t.rstrip().endswith("}") and '"' in t[:80]:
        return "none"
    return None


# ── batch judge ─────────────────────────────────────────────────────────────

def _truncate(text: str) -> str:
    if len(text) <= _HEAD_CHARS + _TAIL_CHARS + 40:
        return text
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return f"{text[:_HEAD_CHARS]}\n…[+{omitted} chars truncated]…\n{text[-_TAIL_CHARS:]}"


def _escape(text: str) -> str:
    # keep item walls unambiguous without full XML escaping
    return text.replace("</item>", "<\\/item>").replace("</user>", "<\\/user>")


def _build_batch_prompt(items: list[dict]) -> str:
    parts = [f"Classify these {len(items)} items.", ""]
    for it in items:
        parts.append(f'<item id="{it["id"]}">')
        prev = it.get("prev_snippet")
        if prev:
            parts.append(f"<prev_assistant>{_escape(prev[-_SNIPPET_CHARS:])}</prev_assistant>")
        parts.append(f"<user>{_escape(_truncate(it['text']))}</user>")
        parts.append("</item>")
    return "\n".join(parts)


def _parse_batch(raw: str, expected_ids: list[int]) -> list[dict] | None:
    """Strict all-or-nothing parse. None on any structural mismatch —
    a wrong id set means alignment can't be trusted; never partial-salvage."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
    try:
        obj = json.loads(s[s.index("{"): s.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    results = obj.get("results")
    if not isinstance(results, list) or len(results) != len(expected_ids):
        return None
    seen: dict[int, dict] = {}
    for r in results:
        if not isinstance(r, dict):
            return None
        rid = r.get("id")
        label = r.get("label")
        machine = r.get("machine_text")
        if rid not in expected_ids or rid in seen:
            return None
        if label not in _VALID_LABELS or not isinstance(machine, bool):
            return None
        if machine:
            label = "none"  # structural invariant: machine text is never friction
        seen[rid] = {"id": rid, "machine_text": machine, "label": label, "judge_error": False}
    return [seen[i] for i in expected_ids]


async def _judge_chunk(items: list[dict]) -> list[dict]:
    """One chunk (<= BATCH_SIZE): call, retry once, bisect, degrade."""
    ids = [it["id"] for it in items]
    prompt = _build_batch_prompt(items)
    raw = await llm.complete(prompt, model=MODEL, system=SYSTEM_PROMPT)
    parsed = _parse_batch(raw, ids)
    if parsed is None:
        raw = await llm.complete(
            prompt + "\n\nYour previous output was malformed. Return ONLY the JSON object "
            "per the output contract — one entry per item id, no prose.",
            model=MODEL, system=SYSTEM_PROMPT,
        )
        parsed = _parse_batch(raw, ids)
    if parsed is not None:
        return parsed
    if len(items) == 1:
        return [{"id": items[0]["id"], "machine_text": False, "label": "none", "judge_error": True}]
    mid = len(items) // 2
    left = await _judge_chunk(items[:mid])
    right = await _judge_chunk(items[mid:])
    return left + right


async def judge_batch(items: list[dict]) -> list[dict]:
    """Classify a list of {"id": int, "text": str, "prev_snippet": str|None}.

    Chunks internally at BATCH_SIZE; returns one JudgeResult per input in
    input order; never raises per-item (poison items degrade to
    judge_error=True, label="none")."""
    out: list[dict] = []
    for i in range(0, len(items), BATCH_SIZE):
        out.extend(await _judge_chunk(items[i:i + BATCH_SIZE]))
    return out


async def classify_pipeline(items: list[dict]) -> list[dict]:
    """The DEPLOYED pipeline: deterministic prefilter short-circuit, then
    batched judge on the remainder. The eval gate measures exactly this."""
    results: dict[int, dict] = {}
    to_judge: list[dict] = []
    for it in items:
        short = prefilter(it["text"], it.get("prev_snippet"))
        if short is not None:
            results[it["id"]] = {
                "id": it["id"], "machine_text": True, "label": short,
                "judge_error": False, "prefiltered": True,
            }
        else:
            to_judge.append(it)
    for r in await judge_batch(to_judge):
        results[r["id"]] = r
    return [results[it["id"]] for it in items]


async def judge(text: str, prev_snippet: str | None = None) -> dict:
    """Compat shim — single-message classification via a one-item batch."""
    (r,) = await judge_batch([{"id": 1, "text": text, "prev_snippet": prev_snippet}])
    return {"machine_text": r["machine_text"], "label": r["label"]}


def version_hash() -> str:
    """Stable hash of every behavior-defining input: prompt, model, batch
    wire format (incl. size), prefilter version. The eval gate records
    this on a TEST pass; the friction sensor refuses any judge whose hash
    has no matching pass marker."""
    import hashlib

    return hashlib.sha256(
        (SYSTEM_PROMPT + "|" + MODEL + "|" + BATCH_FORMAT + "|" + PREFILTER_VERSION).encode()
    ).hexdigest()[:12]
