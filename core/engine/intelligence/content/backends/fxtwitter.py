"""FxTwitter backend — extract tweet content via api.fxtwitter.com.

Free, no-auth mirror that returns full note-tweet text, media, and
engagement metrics. No scraping, no login, no rate drama.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..result import ExtractionError, ExtractionResult


_TWITTER_RE = re.compile(
    r"^https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)",
    re.IGNORECASE,
)


async def extract(
    url: str,
    *,
    platform: str = "",
    timeout: float = 30.0,
) -> ExtractionResult:
    match = _TWITTER_RE.match(url)
    if not match:
        raise ExtractionError("Not a tweet URL", url=url, backend="fxtwitter")

    username, tweet_id = match.group(1), match.group(2)
    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "AOS-Crawler/1.0"},
        ) as client:
            resp = await client.get(api_url)
    except httpx.TimeoutException as e:
        raise ExtractionError(
            f"FxTwitter API timed out: {e}", url=url, backend="fxtwitter"
        ) from e
    except Exception as e:
        raise ExtractionError(
            f"FxTwitter API request failed: {e}", url=url, backend="fxtwitter"
        ) from e

    if resp.status_code != 200:
        raise ExtractionError(
            f"FxTwitter API returned {resp.status_code}",
            url=url,
            backend="fxtwitter",
        )

    try:
        payload = resp.json()
    except Exception as e:
        raise ExtractionError(
            f"FxTwitter API returned invalid JSON: {e}",
            url=url,
            backend="fxtwitter",
        ) from e

    tweet = payload.get("tweet") or {}
    if not tweet:
        raise ExtractionError(
            "FxTwitter response missing tweet payload",
            url=url,
            backend="fxtwitter",
        )

    author = tweet.get("author") or {}
    author_name = (author.get("name") or "").strip()
    screen_name = (author.get("screen_name") or username).strip()

    if author_name:
        title = f"Tweet by {author_name} (@{screen_name})"
        author_str = f"{author_name} (@{screen_name})"
    else:
        title = f"Tweet by @{screen_name}"
        author_str = f"@{screen_name}"

    text = tweet.get("text") or ""
    content = f"# {title}\n\n{text}".strip() if text else title

    media: list[dict[str, Any]] = []
    media_block = tweet.get("media") or {}
    for item in media_block.get("all") or []:
        m_url = item.get("url")
        m_type = item.get("type")
        if not m_url or m_type not in ("photo", "video"):
            continue
        media.append({"type": m_type, "url": m_url})

    metadata = {
        "likes": tweet.get("likes"),
        "retweets": tweet.get("retweets"),
        "views": tweet.get("views"),
        "bookmarks": tweet.get("bookmarks"),
        "is_note_tweet": tweet.get("is_note_tweet"),
        "screen_name": screen_name,
    }

    return ExtractionResult(
        url=tweet.get("url") or url,
        platform="twitter",
        title=title,
        author=author_str,
        content=content,
        published_at=tweet.get("created_at"),
        media=media,
        links=[],
        metadata=metadata,
        backend="fxtwitter",
    )
