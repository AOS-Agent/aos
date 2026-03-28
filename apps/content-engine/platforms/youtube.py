"""YouTube platform handler."""

from .base import BasePlatformHandler
from models import ExtractionResult


class YouTubeHandler(BasePlatformHandler):
    platform = "youtube"

    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        data = self.get_ytdlp_metadata(url) or {}

        return ExtractionResult(
            url=url,
            platform=self.platform,
            content_id=content_id,
            title=data.get("title", ""),
            author=data.get("uploader", "") or data.get("channel", ""),
            author_id=data.get("uploader_id", "") or data.get("channel_id", ""),
            description=data.get("description", ""),
            duration=data.get("duration") or 0,
            hashtags=self.parse_hashtags(data.get("description", "")),
            engagement=self.parse_engagement(data),
            thumbnail_url=data.get("thumbnail", ""),
            content_type=content_type or "video",
            upload_date=data.get("upload_date", ""),
        )
