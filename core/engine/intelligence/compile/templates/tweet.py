"""Tweet capture template — short, authored, engagement-weighted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CaptureTemplate, FrontmatterSpec

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


class TweetTemplate(CaptureTemplate):
    name = "tweet"
    spec = FrontmatterSpec(
        mandatory=[
            "title", "type", "stage", "date", "source_url", "platform",
            "author", "tags", "summary", "concepts", "topic", "source_ref",
            "handle",
        ],
        optional=["likes", "retweets", "views", "bookmarks", "is_note_tweet",
                  "published_at", "media_count"],
    )
    system_prompt_hint = (
        "This is a tweet — short, authored, often a single claim or link drop. "
        "Summarize in ONE sentence. The concept tags should reflect the claim, "
        "not the author. The topic should be the idea being discussed, not 'twitter'."
    )

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        md = extraction.metadata or {}
        extra: dict[str, Any] = {
            "handle": md.get("screen_name", ""),
        }
        if md.get("likes") is not None:
            extra["likes"] = md["likes"]
        if md.get("retweets") is not None:
            extra["retweets"] = md["retweets"]
        if md.get("views") is not None:
            extra["views"] = md["views"]
        if md.get("bookmarks") is not None:
            extra["bookmarks"] = md["bookmarks"]
        if md.get("is_note_tweet") is not None:
            extra["is_note_tweet"] = bool(md["is_note_tweet"])
        if extraction.media:
            extra["media_count"] = len(extraction.media)
        return extra

    def body(
        self,
        *,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> str:
        parts: list[str] = []

        summary = (compilation.get("summary") or "").strip()
        if summary:
            parts.append(f"> {summary}")
            parts.append("")

        content = (extraction.content or "").strip()
        if content:
            parts.append("## Tweet")
            parts.append(content)
            parts.append("")

        md = extraction.metadata or {}
        metrics = []
        if md.get("likes") is not None:
            metrics.append(f"❤ {md['likes']:,}")
        if md.get("retweets") is not None:
            metrics.append(f"🔁 {md['retweets']:,}")
        if md.get("views") is not None and md.get("views"):
            metrics.append(f"👁 {md['views']:,}")
        if metrics:
            parts.append("**Engagement:** " + " · ".join(metrics))
            parts.append("")

        if extraction.media:
            parts.append("## Media")
            for m in extraction.media:
                mtype = m.get("type", "image")
                murl = m.get("url", "")
                parts.append(f"- {mtype}: {murl}")
            parts.append("")

        if extraction.url:
            parts.append(f"**Source:** {extraction.url}")

        return "\n".join(parts).rstrip() + "\n"
