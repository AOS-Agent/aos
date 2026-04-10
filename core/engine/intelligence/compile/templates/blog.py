"""Blog / article capture template — long-form written content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CaptureTemplate, FrontmatterSpec

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


class BlogTemplate(CaptureTemplate):
    name = "blog"
    spec = FrontmatterSpec(
        mandatory=[
            "title", "type", "stage", "date", "source_url", "platform",
            "author", "tags", "summary", "concepts", "topic", "source_ref",
        ],
        optional=["published_at", "word_count", "project"],
    )
    system_prompt_hint = (
        "This is a long-form article or blog post. Summarize the central "
        "argument or main findings in 2-3 sentences. Concept tags should "
        "be the ideas the piece advances, not generic categories."
    )

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        content = extraction.content or ""
        if content:
            wc = len(content.split())
            extra["word_count"] = wc
        return extra
