"""Base platform handler — all platform handlers implement this interface."""

import json
import subprocess
import sys
from abc import ABC, abstractmethod

from models import Engagement, ExtractionResult


class BasePlatformHandler(ABC):
    """Abstract base for platform-specific extraction logic."""

    platform: str = ""

    def get_ytdlp_metadata(self, url: str) -> dict | None:
        """Universal metadata extraction via yt-dlp (works for all platforms)."""
        try:
            result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", "--no-warnings", url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception as e:
            print(f"  yt-dlp metadata failed: {e}", file=sys.stderr)
        return None

    @abstractmethod
    def extract_metadata(self, url: str, content_id: str,
                         content_type: str) -> ExtractionResult:
        """Extract metadata for this platform. Returns a partially filled result."""
        ...

    def parse_engagement(self, data: dict) -> Engagement:
        """Parse engagement metrics from yt-dlp JSON."""
        return Engagement(
            likes=data.get("like_count") or 0,
            comments=data.get("comment_count") or 0,
            views=data.get("view_count") or 0,
            shares=data.get("repost_count") or 0,
        )

    def parse_hashtags(self, text: str) -> list[str]:
        """Extract hashtags from text."""
        if not text:
            return []
        import re
        return re.findall(r'#(\w+)', text)
