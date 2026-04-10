"""ExtractionResult — the one shape all content backends return."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


class ExtractionError(Exception):
    """Raised when a backend cannot produce a usable result.

    Router callers should catch this and decide how to degrade (mark
    content_status='failed' in the DB, show a toast in the UI, etc).
    """

    def __init__(self, message: str, *, url: str = "", backend: str = ""):
        super().__init__(message)
        self.url = url
        self.backend = backend


@dataclass
class ExtractionResult:
    """Unified shape returned by every content backend.

    Fields:
        url           Canonical URL (post-redirect, as the backend saw it).
        platform      Detected platform: twitter, youtube, blog, hn, github...
        title         Document / post / video title.
        author        Author name if known, else empty string.
        content       Full body as markdown. Empty string if unavailable.
        published_at  ISO8601 timestamp if known, else None.
        media         List of {type, url, caption} dicts for images/video/audio.
        links         List of URLs found in the content (for graph building).
        metadata      Backend-specific extras (likes, views, repo stars...).
        backend       Which backend produced this result: crawler, fxtwitter,
                      content_engine.
        fetched_at    ISO8601 timestamp when extraction completed.
    """

    url: str
    platform: str
    title: str = ""
    author: str = ""
    content: str = ""
    published_at: str | None = None
    media: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    backend: str = ""
    fetched_at: str = ""

    def __post_init__(self) -> None:
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation (for API responses, DB storage)."""
        return asdict(self)

    def has_content(self) -> bool:
        """True if the result carries at least some extracted body text."""
        return bool(self.content and self.content.strip())
