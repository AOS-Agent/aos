"""Content extraction backends.

Each backend is a module with an async `extract(url, **opts)` function
that returns an ExtractionResult or raises ExtractionError.

Backends:
    crawler        — crawl4ai for generic web pages
    fxtwitter      — FxTwitter API for tweets
    content_engine — apps/content-engine for deep social media extraction
"""
