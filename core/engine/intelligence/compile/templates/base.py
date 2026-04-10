"""Base capture template — all platform templates extend this.

Design note (Nate Jones discipline): mandatory frontmatter fields are
enumerated per type. A template's job is to (a) tell the LLM what kind of
summary to produce, (b) produce a frontmatter dict, (c) produce the
markdown body. Nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


@dataclass
class FrontmatterSpec:
    """What fields MUST appear in the vault capture's frontmatter."""

    mandatory: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)


class CaptureTemplate:
    """Base class — subclass per platform (tweet, blog, video, ...).

    Subclasses override:
        name                — short type identifier (e.g. "tweet", "blog")
        spec                — FrontmatterSpec for this type
        system_prompt_hint  — one sentence telling the LLM what kind of
                              summary fits this type
        body()              — returns the markdown body for the capture file
        extra_frontmatter() — returns type-specific frontmatter fields
    """

    name: str = "generic"
    spec: FrontmatterSpec = FrontmatterSpec(
        mandatory=["title", "type", "stage", "date", "source_url", "platform",
                   "author", "tags", "summary", "concepts", "topic", "source_ref"],
        optional=["intelligence_id", "relevance_score", "project", "media_count"],
    )
    # Hint passed into the LLM prompt so it knows what kind of capture this is
    system_prompt_hint: str = (
        "Summarize the content in one or two sentences. Focus on the central "
        "idea or claim."
    )

    def build_frontmatter(
        self,
        *,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
        intelligence_id: str | None = None,
        relevance_score: float | None = None,
    ) -> dict[str, Any]:
        """Build a frontmatter dict for this capture.

        Shared fields live here; type-specific extras come from
        `extra_frontmatter()` which subclasses override.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Tags are derived from platform + concepts + topic (dedup-preserving)
        tags = [extraction.platform] if extraction.platform else []
        for c in compilation.get("concepts", []) or []:
            if c and c not in tags:
                tags.append(c)
        topic = compilation.get("topic") or ""
        if topic and topic not in tags:
            tags.append(topic)

        fm: dict[str, Any] = {
            "title": extraction.title or "Untitled",
            "type": "capture",
            "capture_kind": self.name,  # tweet, blog, video, github, paper, generic
            "stage": int(compilation.get("stage_suggestion") or 1),
            "date": today,
            "source_url": extraction.url or "",
            "platform": extraction.platform or "",
            "author": extraction.author or "",
            "tags": tags,
            "summary": (compilation.get("summary") or "").strip(),
            "concepts": list(compilation.get("concepts") or []),
            "topic": topic,
            "source_ref": "feed-ingest" if intelligence_id else "extract-skill",
        }

        if intelligence_id:
            fm["intelligence_id"] = intelligence_id
        if relevance_score is not None:
            fm["relevance_score"] = float(relevance_score)
        if extraction.published_at:
            fm["published_at"] = extraction.published_at

        # Let subclasses layer in type-specific fields
        fm.update(self.extra_frontmatter(extraction, compilation))
        return fm

    def validate_frontmatter(self, frontmatter: dict[str, Any]) -> list[str]:
        """Check a built frontmatter dict against this template's spec.mandatory.

        Returns the list of mandatory keys that are missing or empty. An empty
        list means the frontmatter satisfies the contract. Callers decide what
        to do with drift — the save path logs it so a template that declares a
        mandatory field without populating it (via build_frontmatter or
        extra_frontmatter) surfaces immediately instead of silently drifting.
        """
        missing: list[str] = []
        for key in self.spec.mandatory:
            if key not in frontmatter:
                missing.append(key)
                continue
            value = frontmatter[key]
            # Empty string / empty list / None all count as "not populated".
            if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
                missing.append(key)
        return missing

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        """Type-specific frontmatter fields. Override in subclasses."""
        return {}

    def body(
        self,
        *,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> str:
        """Build the markdown body. Default: summary + content + source."""
        parts: list[str] = []

        summary = (compilation.get("summary") or "").strip()
        if summary:
            parts.append(f"> {summary}")
            parts.append("")

        content = (extraction.content or "").strip()
        if content:
            parts.append("## Content")
            parts.append(content)
            parts.append("")

        if extraction.url:
            parts.append(f"**Source:** {extraction.url}")

        return "\n".join(parts).rstrip() + "\n"
