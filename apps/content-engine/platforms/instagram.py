"""Instagram platform handler."""

from models import ExtractionResult

from .base import BasePlatformHandler


class InstagramHandler(BasePlatformHandler):
    platform = "instagram"

    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        data = self.get_ytdlp_metadata(url) or {}

        description = data.get("description", "") or ""
        channel = data.get("channel", "") or data.get("uploader_id", "") or ""
        uploader = data.get("uploader", "") or channel

        return ExtractionResult(
            url=url,
            platform=self.platform,
            content_id=content_id,
            title=data.get("title", f"Instagram {content_type} {content_id}"),
            author=uploader,
            author_id=f"@{channel}" if channel else "",
            description=description,
            duration=data.get("duration") or 0,
            hashtags=self.parse_hashtags(description),
            engagement=self.parse_engagement(data),
            thumbnail_url=data.get("thumbnail", ""),
            content_type=content_type or "post",
            upload_date=data.get("upload_date", ""),
        )
