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
import json
import sys

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig


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
        output = {
            "url": result.url,
            "status": result.status_code,
            "content": result.markdown.raw_markdown if result.markdown else "",
            "metadata": result.metadata or {},
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
