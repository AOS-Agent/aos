"""Synthesis suggestions — Sonnet drafts stage-3 research proposals.

When a topic has accumulated 3 or more captures but has no research,
synthesis, or decision docs, the lint pass asks Sonnet to draft a
short research proposal: what would a stage-3 research doc about this
topic cover, based on the captures already in hand?

The proposal is appended to the topic index under a "Synthesis suggested"
section. It does NOT auto-create a research file — the operator decides
whether to promote.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIN_CAPTURES_FOR_SYNTHESIS = 3
MAX_SUGGESTIONS_PER_RUN = 10


SYSTEM_PROMPT = """\
You draft research proposals for a personal knowledge vault.

The operator uses a staged knowledge pipeline:
    stage 1: raw captures (tweets, blog posts, videos the operator saved)
    stage 3: research docs (synthesized investigations with conclusions)
    stage 4: synthesis (cross-topic patterns)
    stage 5: decisions (locked conclusions)
    stage 6: expertise (living patterns)

When a topic accumulates several captures but has no stage-3 research \
doc, it's time to consider writing one. Your job: given the captures, \
draft a short proposal for what a stage-3 research doc about this topic \
would cover.

The proposal is for the operator's review — it will NOT auto-create a \
file. Be concrete. Be brief. Be useful.

Constraints:
    - 3-6 sentences of plain prose
    - Identify the ONE question the research would try to answer
    - List 2-4 sub-questions derived from the captures
    - Call out anything that LOOKS contradictory or incomplete across the captures
    - NO fluff, NO boilerplate, NO "I recommend..."

Output: a single JSON object with exactly these fields:
    {
      "central_question": "the main question",
      "sub_questions": ["...", "..."],
      "contradictions": ["..."],
      "proposal": "the 3-6 sentence prose proposal"
    }

No prose outside the JSON. No markdown fence."""


async def draft_synthesis_suggestions(
    *,
    model: str = "sonnet",
    max_topics: int = MAX_SUGGESTIONS_PER_RUN,
) -> dict[str, Any]:
    """Draft synthesis proposals for topics that have enough captures but no research.

    Returns:
        {
            "topics_evaluated": N,
            "suggestions_drafted": N,
            "suggestions": [{topic, proposal, central_question, ...}, ...],
            "errors": [...],
            "total_tokens_in": N,
            "total_tokens_out": N,
        }
    """
    stats: dict[str, Any] = {
        "topics_evaluated": 0,
        "suggestions_drafted": 0,
        "suggestions": [],
        "errors": [],
        "total_tokens_in": 0,
        "total_tokens_out": 0,
    }

    try:
        from ..topics import load_index
        from ..compile.llm import complete, LLMError
    except Exception as e:
        stats["errors"].append(f"setup: {e}")
        return stats

    vault_dir = Path.home() / "vault"
    indexes_dir = vault_dir / "knowledge" / "indexes"
    if not indexes_dir.is_dir():
        return stats

    files = sorted(
        indexes_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    drafted = 0
    for f in files:
        if drafted >= max_topics:
            break

        try:
            idx = load_index(f.stem)
        except Exception as e:
            stats["errors"].append(f"load {f.stem}: {e}")
            continue

        # Eligibility: enough captures, no research/synthesis yet
        if len(idx.captures) < MIN_CAPTURES_FOR_SYNTHESIS:
            continue
        if idx.research or idx.synthesis:
            continue

        stats["topics_evaluated"] += 1

        prompt = _build_synthesis_prompt(idx)
        try:
            response = await complete(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                model=model,
                max_tokens=800,
                response_format="json",
                timeout=90,
            )
        except LLMError as e:
            stats["errors"].append(f"llm {idx.slug}: {e}")
            continue
        except Exception as e:
            stats["errors"].append(f"unexpected {idx.slug}: {e}")
            continue

        stats["total_tokens_in"] += response.tokens_in
        stats["total_tokens_out"] += response.tokens_out

        if not isinstance(response.raw_json, dict):
            stats["errors"].append(f"bad json {idx.slug}")
            continue

        suggestion = {
            "topic_slug": idx.slug,
            "topic_title": idx.title,
            "capture_count": len(idx.captures),
            "central_question": response.raw_json.get("central_question", ""),
            "sub_questions": response.raw_json.get("sub_questions", []),
            "contradictions": response.raw_json.get("contradictions", []),
            "proposal": response.raw_json.get("proposal", ""),
        }

        if suggestion["proposal"]:
            stats["suggestions"].append(suggestion)
            stats["suggestions_drafted"] += 1
            drafted += 1

    return stats


def _build_synthesis_prompt(idx: Any) -> str:
    lines: list[str] = [
        f"# Topic: {idx.title}",
        f"slug: {idx.slug}",
        "",
        f"## Orientation",
        (idx.orientation or "(none)").strip(),
        "",
        f"## Captures ({len(idx.captures)})",
        "",
    ]

    for e in idx.captures[:15]:
        summary = (e.summary or "")[:300]
        lines.append(f"### {e.title}")
        lines.append(f"date: {e.date}")
        if summary:
            lines.append(summary)
        lines.append("")

    lines.append("Produce the JSON response per the system prompt.")
    return "\n".join(lines)
