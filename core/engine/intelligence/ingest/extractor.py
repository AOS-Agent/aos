"""Legacy extractor shim — delegates to the content router.

Historical note: this module used to shell out to crawl_cli.py directly.
Part 2 of the intelligence pipeline rework moved that logic to
`core.engine.intelligence.content.router`, which unifies all extraction
backends behind one entry point.

This shim keeps the old `extract_content(url, platform)` signature alive
for backward compatibility, but all new code should call
`router.extract(url)` directly and handle ExtractionResult / ExtractionError.
"""

from __future__ import annotations

import logging

from ..content import router
from ..content.result import ExtractionError

logger = logging.getLogger(__name__)


async def extract_content(url: str, platform: str = "") -> dict | None:
    """Extract content from a URL via the router.

    Returns the legacy dict shape on success:
        {content, author, title, metadata}
    Returns None on failure (matches the old behavior — callers expected None
    to mean "extraction failed, move on").
    """
    if not url:
        return None
    try:
        result = await router.extract(url)
    except ExtractionError as e:
        logger.warning("extract_content failed for %s: %s", url, e)
        return None

    return {
        "content": result.content,
        "author": result.author,
        "title": result.title,
        "metadata": result.metadata,
    }
