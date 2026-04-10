"""Academic paper template — arXiv and similar."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CaptureTemplate, FrontmatterSpec

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


class PaperTemplate(CaptureTemplate):
    name = "paper"
    spec = FrontmatterSpec(
        mandatory=[
            "title", "type", "stage", "date", "source_url", "platform",
            "author", "tags", "summary", "concepts", "topic", "source_ref",
        ],
        optional=["arxiv_id", "abstract", "published_at", "authors"],
    )
    system_prompt_hint = (
        "This is an academic paper or preprint. Summarize the contribution: "
        "what problem, what method, what result, what matters. Keep it "
        "technical. Concept tags should be the methods/architectures named."
    )

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        url = extraction.url or ""
        # arXiv IDs look like 2401.12345 — pull from /abs/ URL
        if "arxiv.org/abs/" in url:
            arxiv_id = url.split("arxiv.org/abs/", 1)[1].strip("/").split("/")[0]
            if arxiv_id:
                extra["arxiv_id"] = arxiv_id
        return extra
