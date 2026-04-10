"""Content router — single entry point for URL → ExtractionResult.

This is the only function consumers should call. It handles platform
detection, backend selection, error translation, and result normalization.

Usage:
    from core.engine.intelligence.content import router
    result = await router.extract("https://example.com/post")
    if result.has_content():
        ...
"""

from __future__ import annotations

import logging

from .detect import detect_platform, is_tweet_url, is_youtube_video_url
from .result import ExtractionError, ExtractionResult

logger = logging.getLogger(__name__)


async def extract(
    url: str,
    *,
    prefer: str | None = None,
    timeout: float = 30.0,
) -> ExtractionResult:
    """Extract content from a URL, routing to the right backend.

    Args:
        url:     The URL to extract.
        prefer:  Force a specific backend: 'crawler', 'fxtwitter',
                 'content_engine'. Default: auto-detect.
        timeout: Per-backend timeout in seconds.

    Returns:
        ExtractionResult with backend, platform, title, content filled in.

    Raises:
        ExtractionError: if the URL is invalid or the backend cannot produce
                         a usable result.
    """
    if not url or not isinstance(url, str):
        raise ExtractionError("URL is required", url=str(url or ""))

    platform = detect_platform(url)
    backend_name = _pick_backend(url, platform, prefer)

    logger.debug("router.extract url=%s platform=%s backend=%s", url, platform, backend_name)

    backend = _load_backend(backend_name)

    try:
        result = await backend.extract(url, platform=platform, timeout=timeout)
    except ExtractionError:
        raise
    except Exception as e:
        logger.exception("Backend %s failed for %s", backend_name, url)
        raise ExtractionError(
            f"Backend {backend_name} raised {type(e).__name__}: {e}",
            url=url,
            backend=backend_name,
        ) from e

    # Normalize — backends should fill these but we defend anyway
    if not result.url:
        result.url = url
    if not result.platform:
        result.platform = platform
    if not result.backend:
        result.backend = backend_name
    return result


def _pick_backend(url: str, platform: str, prefer: str | None) -> str:
    """Decide which backend to use for a URL."""
    if prefer:
        return prefer

    # Tweet URLs → FxTwitter (free, no auth, returns full note-tweets)
    if is_tweet_url(url):
        return "fxtwitter"

    # Deep social video URLs → content engine (needs Whisper, frame extraction)
    if is_youtube_video_url(url) or platform in ("instagram", "tiktok"):
        return "content_engine"

    # Everything else → crawl4ai
    return "crawler"


def _load_backend(name: str):
    """Lazy-import a backend module by name."""
    if name == "crawler":
        from .backends import crawler
        return crawler
    if name == "fxtwitter":
        from .backends import fxtwitter
        return fxtwitter
    if name == "content_engine":
        from .backends import content_engine
        return content_engine
    raise ExtractionError(f"Unknown backend: {name}", backend=name)
