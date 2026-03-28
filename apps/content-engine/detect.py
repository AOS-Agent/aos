"""URL detection — identify platform and extract content ID from any social media URL."""

import re

# Platform detection patterns: (platform_name, content_id_regex, content_type_hint)
PLATFORM_PATTERNS = [
    # YouTube
    ("youtube", r'(?:youtube\.com/watch\?.*?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})', None),
    ("youtube", r'^([a-zA-Z0-9_-]{11})$', None),  # Raw video ID

    # Instagram
    ("instagram", r'instagram\.com/reel/([A-Za-z0-9_-]+)', "reel"),
    ("instagram", r'instagram\.com/p/([A-Za-z0-9_-]+)', "post"),
    ("instagram", r'instagram\.com/tv/([A-Za-z0-9_-]+)', "igtv"),
    ("instagram", r'instagram\.com/stories/[^/]+/([A-Za-z0-9_-]+)', "story"),

    # TikTok
    ("tiktok", r'tiktok\.com/@[^/]+/video/(\d+)', "video"),
    ("tiktok", r'tiktok\.com/t/([A-Za-z0-9]+)', "video"),  # Short URLs
    ("tiktok", r'vm\.tiktok\.com/([A-Za-z0-9]+)', "video"),  # Mobile share URLs

    # X / Twitter
    ("twitter", r'(?:twitter\.com|x\.com)/\w+/status/(\d+)', "tweet"),
]


def detect_platform(url: str) -> tuple[str | None, str | None, str | None]:
    """Detect platform, content ID, and content type from a URL.

    Returns:
        (platform, content_id, content_type) or (None, None, None) if unrecognized.
    """
    url = url.strip()

    for platform, pattern, content_type in PLATFORM_PATTERNS:
        match = re.search(pattern, url)
        if match:
            content_id = match.group(1)

            # Refine content type for YouTube
            if platform == "youtube":
                if "/shorts/" in url:
                    content_type = "short"
                else:
                    content_type = "video"

            return platform, content_id, content_type

    return None, None, None


def is_social_url(url: str) -> bool:
    """Check if a URL is from a supported social platform."""
    platform, _, _ = detect_platform(url)
    return platform is not None


def extract_urls(text: str) -> list[dict]:
    """Extract all social media URLs from a block of text.

    Returns list of {url, platform, content_id, content_type} dicts.
    """
    # Find all URLs in text
    url_pattern = r'https?://[^\s<>"\')}\]]+'
    urls = re.findall(url_pattern, text)

    results = []
    seen = set()

    for url in urls:
        # Strip trailing punctuation that's not part of the URL
        url = url.rstrip('.,;:!?')

        platform, content_id, content_type = detect_platform(url)
        if platform and content_id not in seen:
            seen.add(content_id)
            results.append({
                "url": url,
                "platform": platform,
                "content_id": content_id,
                "content_type": content_type,
            })

    return results


# Supported platforms for reference
SUPPORTED_PLATFORMS = ["youtube", "instagram", "tiktok", "twitter"]
