"""Video capture template — YouTube, Instagram, TikTok, video-heavy content.

Current status: when the content engine isn't wired for transcript
extraction, this template degrades gracefully — the LLM summarizes the
metadata (title, description) rather than the transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CaptureTemplate, FrontmatterSpec

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


class VideoTemplate(CaptureTemplate):
    name = "video"
    spec = FrontmatterSpec(
        mandatory=[
            "title", "type", "stage", "date", "source_url", "platform",
            "author", "tags", "summary", "concepts", "topic", "source_ref",
        ],
        optional=["duration", "published_at", "has_transcript", "media_count"],
    )
    system_prompt_hint = (
        "This is a video post — could be short-form (reel, tiktok) or "
        "long-form (youtube). Summarize what the video covers based on "
        "whatever content is available (title, description, transcript "
        "if present). If content is thin, say so in the summary."
    )

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        md = extraction.metadata or {}
        extra: dict[str, Any] = {
            "has_transcript": bool(extraction.content and len(extraction.content) > 200),
        }
        if md.get("duration"):
            extra["duration"] = md["duration"]
        if extraction.media:
            extra["media_count"] = len(extraction.media)
        return extra
