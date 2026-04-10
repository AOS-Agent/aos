"""Topic orientation refresh — Sonnet rewrites the orientation paragraph
of each topic index based on the captures currently under that topic.

Topics gain captures over time. The orientation paragraph that Haiku
wrote on first creation (or that the operator wrote by hand) can get
stale. Sonnet reads the current list of captures + research under a
topic and rewrites the orientation to reflect the actual shape of the
knowledge being accumulated.

Skipped when:
    - Topic has fewer than 2 captures (not enough signal)
    - Topic index was updated within the last 24h (too fresh to re-rewrite)
    - LLM call fails or is disabled
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REFRESH_COOLDOWN_HOURS = 24
MIN_CAPTURES_FOR_REFRESH = 2
MAX_TOPICS_PER_RUN = 30


SYSTEM_PROMPT = """\
You rewrite topic orientation paragraphs for a personal knowledge vault.

The operator owns a wiki-style knowledge base organized by topic. Each
topic has an orientation paragraph — a concise, 2-4 sentence description \
of what the topic is about, written as if introducing the topic to someone \
encountering it for the first time.

You will be given:
    - the topic slug (kebab-case identifier)
    - the topic title (human readable)
    - the current orientation paragraph
    - a list of the captures/research/decisions currently under this topic

Your job: produce a NEW orientation paragraph that accurately reflects \
what's accumulated under the topic. Constraints:

    - 2-4 sentences, plain prose
    - Neutral, informative tone (not sales-y, not academic)
    - NO lists, NO markdown, NO headings
    - NO meta references like "this topic covers" or "documents here"
    - Describe the IDEA, not the document collection
    - Keep the operator's voice if the existing orientation has one

If the current orientation is already accurate and specific, return it \
UNCHANGED. Don't rewrite for the sake of rewriting.

Output: a single JSON object with exactly these fields:
    {
      "orientation": "the new or unchanged orientation text",
      "changed": true | false,
      "reason": "brief reason you kept or changed it"
    }

No prose outside the JSON. No markdown fence."""


async def refresh_topic_orientations(
    *,
    model: str = "sonnet",
    max_topics: int = MAX_TOPICS_PER_RUN,
) -> dict[str, Any]:
    """Refresh topic index orientation paragraphs via Sonnet.

    Returns aggregate stats:
        {
            "topics_scanned": N,
            "topics_refreshed": N,
            "topics_unchanged": N,
            "topics_skipped": N,
            "errors": [...],
            "total_tokens_in": N,
            "total_tokens_out": N,
        }
    """
    stats: dict[str, Any] = {
        "topics_scanned": 0,
        "topics_refreshed": 0,
        "topics_unchanged": 0,
        "topics_skipped": 0,
        "errors": [],
        "total_tokens_in": 0,
        "total_tokens_out": 0,
    }

    # Lazy imports — avoid dragging the compile stack unless we're actually linting
    try:
        from ..topics import load_index, update_index
        from ..compile.llm import complete, LLMError
    except Exception as e:
        stats["errors"].append(f"lint setup failed: {e}")
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

    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=REFRESH_COOLDOWN_HOURS)
    processed = 0

    for f in files:
        if processed >= max_topics:
            break
        stats["topics_scanned"] += 1

        # Cooldown check — don't re-rewrite fresh indexes
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if now - mtime < cooldown:
                stats["topics_skipped"] += 1
                continue
        except Exception:
            pass

        try:
            idx = load_index(f.stem)
        except Exception as e:
            stats["errors"].append(f"load {f.stem}: {e}")
            continue

        # Skip if too few captures — not enough signal
        if len(idx.captures) + len(idx.research) < MIN_CAPTURES_FOR_REFRESH:
            stats["topics_skipped"] += 1
            continue

        prompt = _build_refresh_prompt(idx)
        try:
            response = await complete(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                model=model,
                max_tokens=600,
                response_format="json",
                timeout=60,
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

        new_orientation = (response.raw_json.get("orientation") or "").strip()
        changed = bool(response.raw_json.get("changed"))

        if not new_orientation:
            stats["errors"].append(f"empty orientation {idx.slug}")
            continue

        if changed and new_orientation != (idx.orientation or "").strip():
            try:
                update_index(
                    slug=idx.slug,
                    title=idx.title,
                    orientation=new_orientation,
                )
                stats["topics_refreshed"] += 1
            except Exception as e:
                stats["errors"].append(f"write {idx.slug}: {e}")
        else:
            stats["topics_unchanged"] += 1

        processed += 1

    return stats


def _build_refresh_prompt(idx: Any) -> str:
    lines: list[str] = [
        f"# Topic: {idx.title}",
        f"slug: {idx.slug}",
        "",
        "## Current orientation",
        "",
        (idx.orientation or "(empty)").strip(),
        "",
    ]

    if idx.captures:
        lines.append(f"## Captures ({len(idx.captures)})")
        for e in idx.captures[:15]:
            summary = (e.summary or "")[:180]
            lines.append(f"- {e.title} — {summary}")
        lines.append("")

    if idx.research:
        lines.append(f"## Research ({len(idx.research)})")
        for e in idx.research[:10]:
            summary = (e.summary or "")[:180]
            lines.append(f"- {e.title} — {summary}")
        lines.append("")

    if idx.decisions:
        lines.append(f"## Decisions ({len(idx.decisions)})")
        for e in idx.decisions[:5]:
            lines.append(f"- {e.title}")
        lines.append("")

    lines.append("Produce the JSON response per the system prompt.")
    return "\n".join(lines)
