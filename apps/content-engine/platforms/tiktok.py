"""TikTok platform handler.

Note: yt-dlp may be IP-blocked for TikTok. If metadata extraction fails,
the handler returns a minimal result. The engine layer can fall back to
Chrome MCP for full extraction.
"""

from models import ExtractionResult

from .base import BasePlatformHandler


class TikTokHandler(BasePlatformHandler):
    platform = "tiktok"

    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        data = self.get_ytdlp_metadata(url) or {}

        # TikTok yt-dlp may fail due to IP blocking — handle gracefully
        if not data:
            return ExtractionResult(
                url=url,
                platform=self.platform,
                content_id=content_id,
                title=f"TikTok {content_id}",
                content_type=content_type or "video",
                needs_fallback=True,
                fallback_reason="tiktok_ip_blocked",
            )

        description = data.get("description", "") or ""

        return ExtractionResult(
            url=url,
            platform=self.platform,
            content_id=content_id,
            title=data.get("title", ""),
            author=data.get("uploader", "") or data.get("creator", ""),
            author_id=f"@{data.get('uploader_id', '')}" if data.get("uploader_id") else "",
            description=description,
            duration=data.get("duration") or 0,
            hashtags=self.parse_hashtags(description),
            engagement=self.parse_engagement(data),
            thumbnail_url=data.get("thumbnail", ""),
            content_type=content_type or "video",
            upload_date=data.get("upload_date", ""),
        )
