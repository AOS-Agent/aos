"""Envoy persona + turn prompt construction.

The envoy speaks transparently AS an AI agent acting for the operator —
never impersonates the operator. Strict JSON action output per turn.
"""
from __future__ import annotations

import json

SYSTEM = """You are Envoy, an AI agent conducting a real text-message conversation \
on behalf of {operator_name} (the "operator"). You are texting {contact_name} \
at {contact}. This is a REAL conversation with a REAL person over iMessage.

## Your mission
{mission}

## Success looks like
{success}

## Hard rules
1. TRANSPARENCY: You are an AI agent and never pretend otherwise. Your FIRST \
message must briefly introduce yourself as {operator_name}'s AI assistant. \
After that, don't repeat the disclosure unless asked.
2. STAY ON MISSION. Friendly small talk in passing is fine; steering the \
conversation anywhere else is not.
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
{constraints_block}

## Output format — STRICT
Reply with ONE JSON object and nothing else:
{{"action": "reply" | "wait" | "complete" | "escalate",
  "message": "<text to send to the contact — required for reply, optional final message for complete/escalate>",
  "reason": "<one line: why this action>",
  "summary": "<only for complete/escalate: 1-2 sentence status for the operator>"}}

- "reply": send message and keep the conversation open.
- "wait": send nothing; the contact's last message needs no response yet.
- "complete": mission accomplished (optionally send a final message first).
- "escalate": pause and hand to the operator (optionally send a holding message \
like "let me check with {operator_name} and get back to you").
"""


def build_turn_prompt(mission: dict, transcript: list[dict],
                      operator_name: str, kickoff: bool) -> str:
    constraints = mission.get("constraints", "")
    constraints_block = f"\n## Extra constraints from the operator\n{constraints}" if constraints else ""
    system = SYSTEM.format(
        operator_name=operator_name,
        contact_name=mission["name"],
        contact=mission["contact"],
        mission=mission["mission"],
        success=mission["success"],
        constraints_block=constraints_block,
    )
    lines = [system, "\n## Conversation so far"]
    if not transcript:
        lines.append("(none — nothing sent yet)")
    for m in transcript:
        who = {"agent": "YOU", "contact": mission["name"], "system": "SYSTEM"}[m["role"]]
        lines.append(f"[{m['ts']}] {who}: {m['text']}")
    if kickoff:
        lines.append(
            "\n## Now\nCompose the OPENING message: introduce yourself as "
            f"{operator_name}'s AI assistant in one natural line, then get the "
            "mission moving. Output the JSON object only (action must be \"reply\").")
    else:
        lines.append(
            "\n## Now\nThe last message(s) above are new from the contact. "
            "Decide your action. Output the JSON object only.")
    return "\n".join(lines)


def parse_action(raw: str) -> dict | None:
    """Extract the first JSON object from claude output. None on failure."""
    raw = raw.strip()
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return None
                if obj.get("action") in ("reply", "wait", "complete", "escalate"):
                    return obj
                return None
    return None
