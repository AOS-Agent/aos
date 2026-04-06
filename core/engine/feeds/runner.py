#!/usr/bin/env python3
"""runner — Main feed ingest orchestrator.

Called by cron every 30 minutes. Polls RSS feeds, scores items,
extracts content, and stores intelligence briefs to qareen.db.

Usage:
    python runner.py [--db PATH] [--rsshub URL] [--no-extract] [--dry-run]
"""

import asyncio
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchlist import load_sources
from fetcher import fetch_feed
from triage import score_item
from extractor import extract_content
from enricher import summarize
from store import store_item, mark_source_checked, url_exists

DEFAULT_DB = Path.home() / ".aos" / "data" / "qareen.db"
DEFAULT_RSSHUB = "http://localhost:1200"

# Minimum relevance score to proceed with extraction
SCORE_THRESHOLD = 0.1

# Maximum concurrent feed fetches
MAX_CONCURRENT_FEEDS = 5

# Maximum concurrent extractions (heavy — uses browser)
MAX_CONCURRENT_EXTRACTS = 2


async def run_ingest(
    db_path: str | Path | None = None,
    rsshub_base: str = DEFAULT_RSSHUB,
    skip_extract: bool = False,
    dry_run: bool = False,
) -> dict:
    """Main entry point. Called by cron every 30 minutes.

    Flow:
        1. Load active sources from DB
        2. For each source: fetch RSS feed
        3. For each item: dedup by URL
        4. For new items: score relevance
        5. For items above threshold: extract content
        6. Enrich with summary
        7. Store to DB

    Returns stats dict with counts.
    """
    db = str(db_path) if db_path else str(DEFAULT_DB)
    started_at = datetime.now(timezone.utc)

    print(f"[feed-ingest] Starting at {started_at.strftime('%H:%M:%S UTC')}")

    # 1. Load sources
    sources = load_sources(db)
    if not sources:
        print("[feed-ingest] No active sources found")
        return {"sources": 0, "fetched": 0, "new": 0, "stored": 0, "errors": 0}

    print(f"[feed-ingest] Loaded {len(sources)} active sources")

    # Stats
    stats = {
        "sources": len(sources),
        "fetched": 0,
        "new": 0,
        "scored_above_threshold": 0,
        "extracted": 0,
        "stored": 0,
        "duplicates": 0,
        "errors": 0,
    }

    # 2. Fetch feeds concurrently (bounded)
    sem = asyncio.Semaphore(MAX_CONCURRENT_FEEDS)

    async def _fetch_one(source):
        async with sem:
            try:
                items = await fetch_feed(source, rsshub_base)
                return (source, items)
            except Exception as e:
                print(f"[feed-ingest] Error fetching '{source.get('name', '?')}': {e}")
                mark_source_checked(db, source["id"], success=False)
                stats["errors"] += 1
                return (source, [])

    tasks = [_fetch_one(s) for s in sources]
    results = await asyncio.gather(*tasks)

    # 3-7. Process each source's items
    extract_sem = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTS)

    for source, items in results:
        source_name = source.get("name", "?")

        if not items:
            mark_source_checked(db, source["id"], success=False)
            continue

        stats["fetched"] += len(items)
        mark_source_checked(db, source["id"], success=True)
        print(f"  [{source_name}] {len(items)} items")

        for item in items:
            link = item.get("link", "")

            # 3. Dedup by URL
            if link and url_exists(db, link):
                stats["duplicates"] += 1
                continue

            stats["new"] += 1

            # 4. Score relevance
            score, matched_tags = score_item(item, source)

            if score < SCORE_THRESHOLD:
                continue

            stats["scored_above_threshold"] += 1

            # 5. Extract content (unless skipped)
            extracted = None
            content = ""
            author = item.get("author", "")
            title = item.get("title", "Untitled")

            if not skip_extract and link:
                try:
                    async with extract_sem:
                        extracted = await extract_content(link, source.get("platform", ""))
                except Exception as e:
                    print(f"    [extract] Failed for {link}: {e}")

                if extracted:
                    stats["extracted"] += 1
                    content = extracted.get("content", "")
                    # Use extracted author/title if better than RSS
                    if extracted.get("author") and not author:
                        author = extracted["author"]
                    if extracted.get("title") and len(extracted["title"]) > len(title):
                        title = extracted["title"]

            # Fall back to RSS description if no extraction
            if not content:
                content = item.get("description", "")

            # 6. Enrich with summary
            summary = summarize(content)

            # 7. Store
            if dry_run:
                print(f"    [dry-run] Would store: {title[:60]} (score={score:.2f})")
                stats["stored"] += 1
                continue

            brief = {
                "source_id": source["id"],
                "title": title,
                "url": link,
                "summary": summary,
                "content": content,
                "author": author,
                "platform": source.get("platform", ""),
                "layer": source.get("layer", 5),
                "category": source.get("category", "news"),
                "relevance_score": score,
                "relevance_tags": matched_tags,
                "published_at": item.get("published"),
                "project_id": source.get("project_id"),
                "raw_data": item,
            }

            stored = store_item(db, brief)
            if stored:
                stats["stored"] += 1
            else:
                stats["duplicates"] += 1

    # Summary
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    print(f"\n[feed-ingest] Done in {elapsed:.1f}s")
    print(f"  Sources: {stats['sources']}")
    print(f"  Fetched: {stats['fetched']} items")
    print(f"  New:     {stats['new']} (dupes skipped: {stats['duplicates']})")
    print(f"  Scored:  {stats['scored_above_threshold']} above threshold")
    print(f"  Extracted: {stats['extracted']}")
    print(f"  Stored:  {stats['stored']}")
    if stats["errors"]:
        print(f"  Errors:  {stats['errors']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="AOS feed ingest engine")
    parser.add_argument("--db", help="Path to qareen.db", default=None)
    parser.add_argument("--rsshub", help="RSSHub base URL", default=DEFAULT_RSSHUB)
    parser.add_argument("--no-extract", action="store_true", help="Skip content extraction")
    parser.add_argument("--dry-run", action="store_true", help="Score and log but don't store")
    args = parser.parse_args()

    asyncio.run(run_ingest(
        db_path=args.db,
        rsshub_base=args.rsshub,
        skip_extract=args.no_extract,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
