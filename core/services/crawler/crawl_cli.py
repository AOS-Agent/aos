#!/usr/bin/env python3
"""CLI wrapper for crawl4ai — used by skills via Bash.

Usage:
    crawl_cli.py URL [--format markdown|json|html] [--schema NAME]
    crawl_cli.py URL --deep bfs|dfs [--max-pages N]
    crawl_cli.py URL --map [--max-pages N]
    crawl_cli.py --schemas  (list available schemas)
"""

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

_TWITTER_RE = re.compile(r'https?://(?:x\.com|twitter\.com)/(\w+)/status/(\d+)')


@contextlib.contextmanager
def _silence_stdout():
    """Temporarily redirect stdout to /dev/null.

    crawl4ai prints progress banners ([FETCH]..., [SCRAPE].., [COMPLETE]●)
    directly to stdout regardless of verbose=False. When we're emitting JSON
    on stdout, those banners corrupt the output stream. Wrap the crawl call
    in this context manager to silence them, then print the JSON afterward.
    """
    old_stdout = sys.stdout
    devnull = open(os.devnull, "w")
    try:
        sys.stdout = devnull
        yield
    finally:
        sys.stdout = old_stdout
        devnull.close()


async def _fetch_tweet_cli(url: str, output_format: str) -> bool:
    """Handle X/Twitter URLs via FxTwitter API. Returns True if handled."""
    m = _TWITTER_RE.match(url)
    if not m:
        return False
    user, tweet_id = m.groups()
    import httpx
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "AOS-Crawler/1.0"}) as client:
        resp = await client.get(f"https://api.fxtwitter.com/{user}/status/{tweet_id}")
    if resp.status_code != 200:
        print(json.dumps({"error": f"FxTwitter API returned {resp.status_code}"}))
        return True
    data = resp.json()
    tweet = data.get("tweet", {})
    author = tweet.get("author", {})

    if output_format == "json":
        print(json.dumps({
            "url": tweet.get("url", url),
            "author": author.get("name", ""),
            "handle": author.get("screen_name", ""),
            "text": tweet.get("text", ""),
            "created_at": tweet.get("created_at", ""),
            "likes": tweet.get("likes", 0),
            "retweets": tweet.get("retweets", 0),
            "views": tweet.get("views", 0),
            "bookmarks": tweet.get("bookmarks", 0),
            "is_note_tweet": tweet.get("is_note_tweet", False),
        }, indent=2, ensure_ascii=False))
    else:
        print(f"# {author.get('name', '')} (@{author.get('screen_name', '')})")
        print(f"\n*{tweet.get('created_at', '')}*\n")
        print(tweet.get("text", ""))
        print(f"\n---")
        print(f"Likes: {tweet.get('likes', 0):,} | Retweets: {tweet.get('retweets', 0):,} | Views: {tweet.get('views', 0):,}")
    return True


async def crawl_single(url: str, output_format: str = "markdown", schema_name: str = None):
    """Crawl a single page."""
    browser_config = BrowserConfig(headless=True, verbose=False)

    config_kwargs = {"word_count_threshold": 10}

    if schema_name:
        from schemas import SchemaStore
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

        store = SchemaStore()
        schema_def = store.get(schema_name)
        if not schema_def:
            print(json.dumps({"error": f"Schema '{schema_name}' not found"}))
            sys.exit(1)
        config_kwargs["extraction_strategy"] = JsonCssExtractionStrategy(schema_def["selector"])

    config = CrawlerRunConfig(**config_kwargs)

    # Silence crawl4ai's progress banners when we're about to emit structured
    # output (JSON or schema). For human-readable formats the banners are fine.
    needs_silence = output_format == "json" or bool(schema_name)

    if needs_silence:
        with _silence_stdout():
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url, config=config)
    else:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url, config=config)

    if not result.success:
        print(json.dumps({"error": f"Failed to crawl {url}", "status": result.status_code}))
        sys.exit(1)

    if schema_name and result.extracted_content:
        # Schema extraction — output structured data
        print(result.extracted_content)
    elif output_format == "html":
        print(result.html or "")
    elif output_format == "json":
        # Pull title/author from metadata for convenience — the extractor
        # consumes them as top-level fields.
        metadata = result.metadata or {}
        title = metadata.get("title", "") if isinstance(metadata, dict) else ""
        author = metadata.get("author", "") if isinstance(metadata, dict) else ""
        output = {
            "url": result.url,
            "status": result.status_code,
            "title": title,
            "author": author,
            "content": result.markdown.raw_markdown if result.markdown else "",
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        # Default: raw markdown to stdout
        if result.markdown:
            print(result.markdown.raw_markdown)


async def crawl_deep(url: str, strategy: str = "bfs", max_pages: int = 10):
    """Deep crawl a site."""
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, DFSDeepCrawlStrategy

    browser_config = BrowserConfig(headless=True, verbose=False)

    if strategy == "dfs":
        crawl_strategy = DFSDeepCrawlStrategy(max_depth=3, max_pages=max_pages)
    else:
        crawl_strategy = BFSDeepCrawlStrategy(max_depth=2, max_pages=max_pages)

    config = CrawlerRunConfig(
        deep_crawl_strategy=crawl_strategy,
        word_count_threshold=10,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun(url, config=config)

    if not isinstance(results, list):
        results = [results]

    for r in results:
        if r.success and r.markdown:
            print(f"\n{'='*60}")
            print(f"# {(r.metadata or {}).get('title', r.url)}")
            print(f"URL: {r.url}")
            print(f"{'='*60}\n")
            print(r.markdown.raw_markdown[:2000])


async def crawl_map(url: str, max_pages: int = 50):
    """Map a site — discover URLs without extracting content."""
    browser_config = BrowserConfig(headless=True, verbose=False)
    config = CrawlerRunConfig(word_count_threshold=1)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url, config=config)

    if not result.success:
        print(json.dumps({"error": f"Failed to crawl {url}"}))
        sys.exit(1)

    links = []
    if result.links:
        for link in result.links.get("internal", []):
            href = link.get("href", "") if isinstance(link, dict) else str(link)
            if href:
                links.append(href)

    for link in links[:max_pages]:
        print(link)


def list_schemas():
    """List available extraction schemas."""
    from schemas import SchemaStore
    store = SchemaStore()
    schemas = store.list_all()
    if not schemas:
        print("No schemas found. Schemas are stored in ~/.aos/data/crawler/schemas/")
        return
    for s in schemas:
        print(f"  {s['name']:30s} {s['domain']:30s} {s['description']}")


def main():
    parser = argparse.ArgumentParser(description="AOS web crawler CLI")
    parser.add_argument("url", nargs="?", help="URL to crawl")
    parser.add_argument("--format", choices=["markdown", "json", "html"], default="markdown")
    parser.add_argument("--schema", help="Named extraction schema to apply")
    parser.add_argument("--deep", choices=["bfs", "dfs"], help="Deep crawl strategy")
    parser.add_argument("--map", action="store_true", help="Map site URLs only")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages for deep/map")
    parser.add_argument("--schemas", action="store_true", help="List available schemas")

    args = parser.parse_args()

    if args.schemas:
        list_schemas()
        return

    if not args.url:
        parser.print_help()
        sys.exit(1)

    if args.map:
        asyncio.run(crawl_map(args.url, args.max_pages))
    elif args.deep:
        asyncio.run(crawl_deep(args.url, args.deep, args.max_pages))
    else:
        asyncio.run(crawl_single(args.url, args.format, args.schema))


if __name__ == "__main__":
    main()
