"""Haiku prompts for per-capture compilation (Pass 2).

The compilation pass takes an extracted piece of content and returns a
structured JSON payload: summary, concepts, topic assignment, entities,
related captures, stage suggestion.

Karpathy trick: we read existing topic index summaries into the prompt
so the LLM "pre-knows" the landscape at write-time and can match new
captures into existing topics instead of inventing parallel ones.

The output schema is a strict JSON contract. The parser is intolerant —
the prompt compensates by being explicit and the llm.py wrapper has a
one-shot retry with a reformat nudge.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You are the compilation engine for a personal knowledge vault.

Your job: given one piece of external content (a tweet, blog post, paper, \
video, repo, whatever), produce a strict JSON compilation record that \
lets the vault classify, link, and surface it later. You are NOT a \
general chat assistant. You never greet, never apologize, never explain.

You always return a single JSON object matching the schema below. No \
prose, no markdown fence, no trailing comments.

The operator values:
- atomic thinking (one idea per note)
- precise topic assignment (match existing topics where possible)
- machine-readable metadata (every concept tag is a searchable handle)
- concision over comprehensiveness

When choosing a topic, match an existing topic from the provided list if \
the content clearly belongs there. Only invent a new topic when the \
content genuinely does not fit any existing one.

Concepts are 3-8 short tag-like strings (lowercase, hyphen-separated, \
1-3 words each). They should be the ideas, not the categories. Prefer \
"transformer-scaling-laws" over "ai".

Entities are people, projects, organizations, or products explicitly \
named in the content. Skip vague references.

Your output must parse as JSON on the first try. If you are unsure of \
a field, use a safe default (empty string, empty list, 0.5 confidence) \
rather than omitting the field.\
"""


RESPONSE_SCHEMA = {
    "summary": "string — 1 to 3 sentences, the central idea or claim",
    "concepts": "list of 3-8 short lowercase tag strings",
    "topic": "string — topic slug (lowercase, hyphens), matches existing or new",
    "topic_confidence": "float 0.0-1.0",
    "topic_is_new": "bool — true if you're proposing a topic not in the existing list",
    "entities": [
        {
            "type": "string — one of: person, project, organization, product",
            "name": "string — the entity name",
            "confidence": "float 0.0-1.0",
        }
    ],
    "related_captures": "list of capture paths from the provided list that this new capture relates to; empty list if none clearly relate",
    "stage_suggestion": "int — almost always 1 (stage-1 capture); 2 only if the content is already substantially processed (a paper's abstract, a well-written synthesis)",
}


def build_compile_prompt(
    *,
    platform: str,
    title: str,
    author: str,
    url: str,
    content: str,
    template_hint: str,
    existing_topics: list[dict],
    recent_capture_paths: list[str],
) -> str:
    """Build the user prompt for the Haiku compilation call.

    existing_topics: list of {slug, title, orientation, doc_count} dicts —
                     the LLM will match the new capture to one of these
                     when possible. Keep this list <= 25 items (the prompt
                     budget; more and we pay for tokens that don't help).
    recent_capture_paths: list of recent capture file paths (just the slugs)
                          so the LLM can suggest related_captures from a
                          known set, not invent paths.
    """
    # Trim content — Haiku context is fine but we don't need the whole thing
    trimmed_content = (content or "")[:8000]
    if content and len(content) > 8000:
        trimmed_content += "\n\n...[content truncated for compilation]"

    topics_block = _format_topics_block(existing_topics)
    captures_block = _format_captures_block(recent_capture_paths)

    schema_json = json.dumps(RESPONSE_SCHEMA, indent=2)

    return f"""\
# Capture to compile

**Platform:** {platform or "unknown"}
**Template hint:** {template_hint}
**Title:** {title or "(no title)"}
**Author:** {author or "(unknown)"}
**Source URL:** {url or "(none)"}

## Content

{trimmed_content or "(no content extracted)"}

---

## Existing topics in the vault ({len(existing_topics)})

{topics_block}

## Recent captures ({len(recent_capture_paths)})

{captures_block}

---

## Task

Produce a single JSON object matching this schema exactly:

```
{schema_json}
```

Return ONLY the JSON object. No prose.\
"""


def _format_topics_block(topics: list[dict]) -> str:
    if not topics:
        return "(none yet — the vault is cold, feel free to propose a new topic)"
    lines: list[str] = []
    for t in topics[:25]:
        slug = t.get("slug", "")
        title = t.get("title", "")
        orientation = (t.get("orientation") or "").replace("\n", " ").strip()
        count = t.get("doc_count", 0)
        if len(orientation) > 160:
            orientation = orientation[:157] + "..."
        lines.append(f"- **{slug}** ({count} docs): {title} — {orientation}")
    return "\n".join(lines)


def _format_captures_block(paths: list[str]) -> str:
    if not paths:
        return "(none — this is one of the first captures in the vault)"
    return "\n".join(f"- {p}" for p in paths[:40])
