"""intelligence.content — Unified content extraction router.

Single entry point for turning a URL into an ExtractionResult, regardless
of what the URL actually is. Consumers (feed ingest, extract skill,
compilation engine, API endpoints) call `router.extract(url)` and get
back a consistent shape.

Backends:
    crawler       — crawl4ai via crawl_cli.py (default)
    fxtwitter     — x.com / twitter.com via FxTwitter API
    content_engine — YouTube/IG/TikTok deep extraction via apps/content-engine

The router picks a backend based on URL detection. Consumers never
import backends directly.

Usage:
    from core.engine.intelligence.content import router, ExtractionResult
    result = await router.extract("https://simonwillison.net/2024/Dec/31/")
"""

from .result import ExtractionResult, ExtractionError
from . import router

__all__ = ["ExtractionResult", "ExtractionError", "router"]
