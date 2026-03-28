"""X/Twitter platform handler.

yt-dlp handles video tweets well but can't extract text-only tweets.
For text-only tweets, the engine falls back to Chrome MCP (get_page_text).
"""

from .base import BasePlatformHandler
from models import ExtractionResult


class TwitterHandler(BasePlatformHandler):
    platform = "twitter"

    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        data = self.get_ytdlp_metadata(url) or {}

        # yt-dlp only works for video tweets — text-only returns empty
        if not data:
            return ExtractionResult(
                url=url,
                platform=self.platform,
                content_id=content_id,
                title=f"Tweet {content_id}",
                content_type="tweet",
            )

        description = data.get("description", "") or ""

        return ExtractionResult(
            url=url,
            platform=self.platform,
            content_id=content_id,
            title=data.get("title", ""),
            author=data.get("uploader", ""),
            author_id=f"@{data.get('uploader_id', '')}" if data.get("uploader_id") else "",
            description=description,
            duration=data.get("duration") or 0,
            hashtags=self.parse_hashtags(description),
            engagement=self.parse_engagement(data),
            thumbnail_url=data.get("thumbnail", ""),
            content_type="tweet",
            upload_date=data.get("upload_date", ""),
        )
