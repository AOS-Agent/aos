"""Synthesis — turn a finished council into a vault decision memo.

Called automatically at adjournment. Output:
  ~/vault/knowledge/decisions/<date>-<slug>-council.md

With frontmatter so QMD indexes it as a decision-stage knowledge artifact.
Also extracts a one-paragraph summary for Telegram delivery.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from .chat import Chat

VAULT_DECISIONS = Path.home() / "vault" / "knowledge" / "decisions"


def synthesize(council_id: str, topic: str, personas: list[str], chat: Chat) -> dict:
    """Generate a synthesis memo from the council transcript.

    Uses `claude -p` to compose the synthesis in chief-of-staff register.
    Returns dict with keys: memo_path, summary, verdict, dissent.
    """
    msgs = chat.read()
    if not msgs:
        return {"memo_path": None, "summary": "Council adjourned with no turns.", "verdict": "", "dissent": ""}

    transcript_lines = []
    for m in msgs:
        addr = f" → @{m.addressed_to}" if m.addressed_to else ""
        transcript_lines.append(f"[{m.speaker}{addr}]\n{m.body}")
    transcript = "\n\n".join(transcript_lines)

    persona_str = ", ".join(personas)

    synthesis_prompt = f"""You are the Coordinator (Chief) writing a synthesis memo for the operator after a council adjourned. Personas in this council: {persona_str}.

TOPIC: {topic}

FULL TRANSCRIPT:
{transcript}

WRITE THE MEMO. Structure (use these exact section headers):

## Verdict
One paragraph. The actionable answer. What the operator should do.

## Reasoning
2-3 paragraphs. Show how the council reached this through cross-engagement. Name agents by lens, not by name ("the architect lens", "the skeptic lens"). Highlight the moves that shifted the answer.

## Dissent and open questions
Bullet list. Name disagreements that did not resolve and questions the council surfaced but did not answer.

## What to lock in before action
Bullet list. The non-negotiables — schema requirements, contract rules, anything the operator must commit to before code lands.

Write in chief-of-staff register: confident, signed, brief. Plain prose, no headers other than the four above. Do not invent details not in the transcript. End the memo with the line: === END MEMO ==="""

    import subprocess
    proc = subprocess.run(
        ["claude", "-p", synthesis_prompt],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"synthesis failed: {proc.stderr[:300]}")
    memo_body = proc.stdout.strip()
    # Strip the END MEMO marker
    memo_body = memo_body.replace("=== END MEMO ===", "").strip()

    # Extract a one-paragraph summary from the Verdict section
    summary = ""
    for line in memo_body.splitlines():
        if line.strip().startswith("## Verdict"):
            continue
        if line.strip().startswith("##"):
            break
        if line.strip():
            summary += line.strip() + " "
        elif summary:
            break
    summary = summary.strip()

    # Write to vault
    VAULT_DECISIONS.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    memo_path = VAULT_DECISIONS / f"{today}-{council_id}-council.md"
    frontmatter = f"""---
title: "Council — {topic}"
type: decision
date: {today}
council_id: {council_id}
personas: [{', '.join(personas)}]
turns: {len(msgs)}
stage: 5
tags: [council, decision]
source_ref: "~/.aos/data/councils/{council_id}/chat.jsonl"
---

# Council — {topic}

{memo_body}

---

## Council members
- {chr(10).join(f"- {p}" for p in personas)}

## Transcript
Full chat at `~/.aos/data/councils/{council_id}/chat.jsonl` ({len(msgs)} turns).
"""
    memo_path.write_text(frontmatter)

    return {
        "memo_path": str(memo_path),
        "summary": summary or memo_body[:300],
        "memo_body": memo_body,
    }
