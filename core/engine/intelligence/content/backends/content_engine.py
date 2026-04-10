"""content_engine backend — deep social media extraction.

Target platforms: YouTube, Instagram, TikTok. Uses the apps/content-engine
pipeline (yt-dlp + Whisper + storyboard frames + OCR) for full extraction.

Current status: DELEGATES to the crawler backend as a fallback. The full
content-engine integration requires invoking its separate venv and pipeline
via subprocess; that lives in a later phase. For now, YouTube/IG/TikTok URLs
still return a usable ExtractionResult via crawl4ai (page metadata only, no
transcript or frames).

When the full integration lands, replace `extract` here with a subprocess
call to `apps/content-engine/cli.py <url> --json` and map its output to
ExtractionResult.
"""

from __future__ import annotations

import logging

from ..result import ExtractionError, ExtractionResult
from . import crawler

logger = logging.getLogger(__name__)


async def extract(
    url: str,
    *,
    platform: str = "",
    timeout: float = 30.0,
) -> ExtractionResult:
    """Extract a social media URL via the content engine pipeline.

    Currently delegates to the crawler backend. Marks the result with
    backend='content_engine_fallback' so consumers can tell when deep
    extraction is missing.
    """
    try:
        result = await crawler.extract(url, platform=platform, timeout=timeout)
    except ExtractionError as e:
        # Re-raise but attribute to this backend so consumers can see
        # which backend was *picked* by the router, not the one that fell back.
        raise ExtractionError(str(e), url=url, backend="content_engine") from e

    result.backend = "content_engine_fallback"
    result.metadata.setdefault("fallback_reason", "content_engine_pipeline_not_wired")
    return result
