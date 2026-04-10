"""GitHub capture template — repos, releases, issues."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CaptureTemplate, FrontmatterSpec

if TYPE_CHECKING:
    from ...content.result import ExtractionResult


class GitHubTemplate(CaptureTemplate):
    name = "github"
    spec = FrontmatterSpec(
        mandatory=[
            "title", "type", "stage", "date", "source_url", "platform",
            "author", "tags", "summary", "concepts", "topic", "source_ref",
        ],
        optional=["repo", "stars", "language", "license", "published_at"],
    )
    system_prompt_hint = (
        "This is a GitHub page — a repo, release, or issue. Summarize what "
        "the project DOES or what the change CHANGES. Concept tags should "
        "include the primary language and the problem domain."
    )

    def extra_frontmatter(
        self,
        extraction: "ExtractionResult",
        compilation: dict[str, Any],
    ) -> dict[str, Any]:
        # GitHub URLs look like https://github.com/<owner>/<repo>[/...]
        # Try to pull the owner/repo from the URL.
        extra: dict[str, Any] = {}
        url = extraction.url or ""
        if "github.com/" in url:
            try:
                path = url.split("github.com/", 1)[1].strip("/")
                parts = path.split("/")
                if len(parts) >= 2:
                    extra["repo"] = f"{parts[0]}/{parts[1]}"
            except Exception:
                pass
        md = extraction.metadata or {}
        if md.get("language"):
            extra["language"] = md["language"]
        if md.get("license"):
            extra["license"] = md["license"]
        return extra
