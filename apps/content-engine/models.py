"""Data models for the content extraction pipeline."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Engagement:
    likes: int = 0
    comments: int = 0
    views: int = 0
    shares: int = 0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v > 0}


@dataclass
class SourceContext:
    """Where this URL came from (if detected in messages)."""
    sender: str = ""
    message: str = ""
    chat: str = ""
    platform: str = ""  # whatsapp | imessage | telegram
    timestamp: str = ""


@dataclass
class ExtractionResult:
    url: str
    platform: str  # youtube | instagram | tiktok | twitter
    content_id: str  # Video ID, shortcode, tweet ID, etc.

    # Metadata (Tier 1 — always populated after metadata extraction)
    title: str = ""
    author: str = ""
    author_id: str = ""  # @handle
    description: str = ""
    duration: int = 0  # seconds
    hashtags: list[str] = field(default_factory=list)
    engagement: Engagement = field(default_factory=Engagement)
    thumbnail_url: str = ""
    content_type: str = ""  # video | reel | short | post | tweet | story
    upload_date: str = ""

    # Content (Tier 2 — populated on deep extraction)
    transcript: str | None = None
    transcript_source: str = ""  # captions | whisper | none

    # Routing context
    source_context: SourceContext | None = None

    # Output tracking
    vault_path: str | None = None
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def has_transcript(self) -> bool:
        return self.transcript is not None and len(self.transcript.strip()) > 0

    @property
    def has_audio(self) -> bool:
        return self.duration > 0

    def to_dict(self) -> dict:
        d = {
            "url": self.url,
            "platform": self.platform,
            "content_id": self.content_id,
            "title": self.title,
            "author": self.author,
            "author_id": self.author_id,
            "description": self.description,
            "duration": self.duration,
            "hashtags": self.hashtags,
            "engagement": self.engagement.to_dict(),
            "thumbnail_url": self.thumbnail_url,
            "content_type": self.content_type,
            "upload_date": self.upload_date,
            "transcript": self.transcript,
            "transcript_source": self.transcript_source,
            "vault_path": self.vault_path,
            "extracted_at": self.extracted_at,
        }
        if self.source_context:
            d["source_context"] = self.source_context.__dict__
        return d
