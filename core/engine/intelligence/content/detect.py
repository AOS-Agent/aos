"""URL platform detection.

Given a URL, return a platform tag that the router uses to pick a backend.
Keep this file dependency-free — pure string/regex logic only.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Known platform signatures. Order matters — first match wins.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("twitter",   re.compile(r"(?:^|\.)(?:x|twitter)\.com$", re.I)),
    ("youtube",   re.compile(r"(?:^|\.)(?:youtube\.com|youtu\.be)$", re.I)),
    ("instagram", re.compile(r"(?:^|\.)instagram\.com$", re.I)),
    ("tiktok",    re.compile(r"(?:^|\.)tiktok\.com$", re.I)),
    ("reddit",    re.compile(r"(?:^|\.)reddit\.com$", re.I)),
    ("github",    re.compile(r"(?:^|\.)github\.com$", re.I)),
    ("hn",        re.compile(r"(?:^|\.)news\.ycombinator\.com$", re.I)),
    ("arxiv",     re.compile(r"(?:^|\.)arxiv\.org$", re.I)),
    ("substack",  re.compile(r"(?:^|\.)substack\.com$", re.I)),
    ("mastodon",  re.compile(r"(?:^|\.)(?:mastodon\.social|fosstodon\.org)$", re.I)),
]


def detect_platform(url: str) -> str:
    """Classify a URL into a platform tag.

    Returns one of: twitter, youtube, instagram, tiktok, reddit, github, hn,
    arxiv, substack, mastodon, blog (default).

    Returns 'unknown' only if the URL fails to parse.
    """
    if not url:
        return "unknown"
    try:
        parsed = urlparse(url)
    except Exception:
        return "unknown"

    host = (parsed.hostname or "").lower()
    if not host:
        return "unknown"

    for tag, pattern in _PATTERNS:
        if pattern.search(host):
            return tag

    return "blog"


def is_tweet_url(url: str) -> bool:
    """True if the URL points at a specific tweet (not a profile)."""
    return bool(re.match(
        r"^https?://(?:www\.)?(?:x|twitter)\.com/\w+/status/\d+",
        url or "",
        re.I,
    ))


def is_youtube_video_url(url: str) -> bool:
    """True if the URL points at a specific YouTube video."""
    if not url:
        return False
    return bool(
        re.match(r"^https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+", url, re.I)
        or re.match(r"^https?://youtu\.be/[\w-]+", url, re.I)
    )
