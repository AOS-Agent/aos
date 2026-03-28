"""YouTube platform handler.

Extracts metadata, chapters, and top comments via yt-dlp.
"""

import json
import subprocess
import sys

from .base import BasePlatformHandler
from models import ExtractionResult


class YouTubeHandler(BasePlatformHandler):
    platform = "youtube"

    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        data = self.get_ytdlp_metadata(url) or {}

        # Parse chapters from yt-dlp JSON
        chapters = []
        for ch in data.get("chapters") or []:
            title = ch.get("title", "")
            if title and not title.startswith("<Untitled"):
                chapters.append({
                    "start_time": int(ch.get("start_time", 0)),
                    "title": title,
                })

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
            chapters=chapters,
        )

    def fetch_comments(self, url: str, max_comments: int = 20) -> list[dict]:
        """Fetch top comments via yt-dlp --write-comments.

        Separate call from metadata to keep metadata fast.
        Returns list of {author, text, like_count}.
        """
        try:
            result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", "--no-warnings",
                 "--write-comments",
                 "--extractor-args", f"youtube:max_comments={max_comments}",
                 url],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return []

            data = json.loads(result.stdout)
            comments = []
            for c in data.get("comments") or []:
                text = (c.get("text") or "").strip()
                if not text:
                    continue
                comments.append({
                    "author": c.get("author", ""),
                    "text": text,
                    "like_count": c.get("like_count") or 0,
                })

            # Sort by likes descending — surface the most valued comments
            comments.sort(key=lambda x: x["like_count"], reverse=True)
            return comments

        except Exception as e:
            print(f"  Comment fetch failed: {e}", file=sys.stderr)
            return []
