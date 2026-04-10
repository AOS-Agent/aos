"""fetcher — Fetch RSS feeds from RSSHub or native RSS URLs."""

import asyncio
from datetime import datetime, timezone

import feedparser
import httpx

# Timeout for individual feed fetches
FETCH_TIMEOUT = 20  # seconds


async def fetch_feed(
    source: dict,
    rsshub_base: str = "http://localhost:1200",
) -> list[dict]:
    """Fetch RSS feed for a source.

    If source has `route`: prepend rsshub_base (e.g. /twitter/user/karpathy).
    If source has `route_url`: use directly (native RSS).

    Returns list of dicts: {title, link, published, description, author}.
    Returns empty list on any error.
    """
    feed_url = _resolve_feed_url(source, rsshub_base)
    if not feed_url:
        print(f"[fetcher] No route or route_url for source '{source.get('name', '?')}'")
        return []

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "AOS-FeedIngest/1.0"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(feed_url)
            resp.raise_for_status()
            raw_xml = resp.text
    except httpx.TimeoutException:
        print(f"[fetcher] Timeout fetching {feed_url}")
        return []
    except httpx.HTTPStatusError as e:
        print(f"[fetcher] HTTP {e.response.status_code} from {feed_url}")
        return []
    except Exception as e:
        print(f"[fetcher] Error fetching {feed_url}: {e}")
        return []

    return _parse_feed(raw_xml, source)


def _resolve_feed_url(source: dict, rsshub_base: str) -> str | None:
    """Determine the feed URL from source config."""
    route = source.get("route")
    route_url = source.get("route_url")

    if route:
        # RSSHub route — prepend base URL
        base = rsshub_base.rstrip("/")
        path = route if route.startswith("/") else f"/{route}"
        return f"{base}{path}"
    elif route_url:
        # Direct RSS URL
        return route_url
    elif source.get("url"):
        # Fallback: try the base URL itself as an RSS feed
        return source["url"]
    return None


def _parse_feed(raw_xml: str, source: dict) -> list[dict]:
    """Parse RSS/Atom XML into a list of item dicts."""
    try:
        feed = feedparser.parse(raw_xml)
    except Exception as e:
        print(f"[fetcher] Parse error for '{source.get('name', '?')}': {e}")
        return []

    if feed.bozo and not feed.entries:
        print(f"[fetcher] Malformed feed for '{source.get('name', '?')}': {feed.bozo_exception}")
        return []

    items = []
    for entry in feed.entries:
        published = _extract_published(entry)
        items.append({
            "title": (entry.get("title") or "").strip(),
            "link": (entry.get("link") or "").strip(),
            "published": published,
            "description": _extract_description(entry),
            "author": (
                entry.get("author")
                or entry.get("dc_creator")
                or (entry.get("authors", [{}])[0].get("name") if entry.get("authors") else "")
                or ""
            ).strip(),
        })

    return items


def _extract_published(entry: dict) -> str | None:
    """Extract and normalize the published date from a feed entry."""
    # feedparser provides parsed time tuples
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue

    # Fall back to raw string
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if raw:
            return str(raw).strip()

    return None


def _extract_description(entry: dict) -> str:
    """Extract a clean text description from a feed entry."""
    # Prefer summary, fall back to content
    desc = entry.get("summary") or ""
    if not desc and entry.get("content"):
        contents = entry["content"]
        if isinstance(contents, list) and contents:
            desc = contents[0].get("value", "")

    # Strip HTML tags (basic)
    if "<" in desc:
        import re
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()

    return desc[:2000]  # cap description length
