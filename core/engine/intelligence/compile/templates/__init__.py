"""Capture templates — Nate Jones discipline layer.

Different capture types force different thinking. A tweet and a research
paper should NOT have the same frontmatter shape — the fields required
for each reflect the thinking patterns appropriate to that kind of input.

Each template defines:
    - which frontmatter fields are mandatory for this type
    - the markdown body layout
    - a system prompt hint for the compile engine (so the LLM knows what
      kind of summary is wanted for this type)

Templates are selected from platform + detect hints. The registry is a
simple dict; the fallback is `generic`.
"""

from __future__ import annotations

from .base import CaptureTemplate, FrontmatterSpec
from .blog import BlogTemplate
from .generic import GenericTemplate
from .github import GitHubTemplate
from .paper import PaperTemplate
from .tweet import TweetTemplate
from .video import VideoTemplate


# Registry: platform tag → template class
# (platform comes from ExtractionResult.platform, which came from detect.py)
_REGISTRY: dict[str, type[CaptureTemplate]] = {
    "twitter": TweetTemplate,
    "youtube": VideoTemplate,
    "instagram": VideoTemplate,
    "tiktok": VideoTemplate,
    "github": GitHubTemplate,
    "arxiv": PaperTemplate,
    "blog": BlogTemplate,
    "substack": BlogTemplate,
    "hn": BlogTemplate,  # HN stories are usually blog-shaped
    "reddit": BlogTemplate,
}


def get_template(platform: str) -> CaptureTemplate:
    """Look up a template by platform tag, falling back to generic."""
    cls = _REGISTRY.get((platform or "").lower(), GenericTemplate)
    return cls()


__all__ = [
    "CaptureTemplate",
    "FrontmatterSpec",
    "get_template",
]
