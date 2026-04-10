#!/usr/bin/env python3
"""runner — Feed ingest orchestrator.

Fast, dumb ingest: fetch RSS → dedup → score → store metadata.
No LLM, no extraction, no browser. Runs in <1 second per source.

Full content extraction is deferred to the on-demand extract endpoint
(POST /api/intelligence/items/{id}/extract) which is invoked when the
operator opens an item. This keeps the cron path tight and keeps the
ingest loop resilient to flaky content fetchers.

Usage:
    python runner.py [--db PATH] [--rsshub URL] [--dry-run]

Can also be run as a module:
    python -m core.engine.intelligence.ingest.runner
"""

import asyncio
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Prefer relative imports when run as a module; fall back to bare imports
# when the script is executed directly (directory on sys.path).
try:
    from .watchlist import load_sources
    from .fetcher import fetch_feed
    from .triage import score_item
    from .enricher import summarize
    from .store import store_item, mark_source_checked, url_exists
except ImportError:
    from watchlist import load_sources  # type: ignore[no-redef]
    from fetcher import fetch_feed  # type: ignore[no-redef]
    from triage import score_item  # type: ignore[no-redef]
    from enricher import summarize  # type: ignore[no-redef]
    from store import store_item, mark_source_checked, url_exists  # type: ignore[no-redef]

DEFAULT_DB = Path.home() / ".aos" / "data" / "qareen.db"
DEFAULT_RSSHUB = "http://localhost:1200"

# Minimum relevance score required to keep an item
SCORE_THRESHOLD = 0.1

# Concurrent feed fetches (I/O bound, httpx)
MAX_CONCURRENT_FEEDS = 5


async def run_ingest(
    db_path: str | Path | None = None,
    rsshub_base: str = DEFAULT_RSSHUB,
    dry_run: bool = False,
) -> dict:
    """Main entry point. Called by cron every 30 minutes.

    Flow:
        1. Load active sources from DB
        2. Fetch RSS feeds concurrently
        3. For each new item (by URL): score relevance
        4. Above threshold → store metadata with content_status='pending'

    Extraction happens later, on demand, via the API. This function never
    calls an LLM or a browser. Target: <1 second total for a full cycle.

    Returns stats dict with counts.
    """
    db = str(db_path) if db_path else str(DEFAULT_DB)
    started_at = datetime.now(timezone.utc)

    print(f"[feed-ingest] Starting at {started_at.strftime('%H:%M:%S UTC')}")

    sources = load_sources(db)
    if not sources:
        print("[feed-ingest] No active sources found")
        return {"sources": 0, "fetched": 0, "new": 0, "stored": 0, "errors": 0}

    print(f"[feed-ingest] Loaded {len(sources)} active sources")

    stats = {
        "sources": len(sources),
        "fetched": 0,
        "new": 0,
        "scored_above_threshold": 0,
        "stored": 0,
        "duplicates": 0,
        "errors": 0,
    }

    # Fetch feeds concurrently (bounded)
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

            # Dedup by URL
            if link and url_exists(db, link):
                stats["duplicates"] += 1
                continue

            stats["new"] += 1

            # Score relevance (cheap — keyword matching)
            score, matched_tags = score_item(item, source)
            if score < SCORE_THRESHOLD:
                continue

            stats["scored_above_threshold"] += 1

            title = item.get("title", "Untitled")
            author = item.get("author", "")

            # Store RSS description as the initial summary. Real summarization
            # happens in Pass 2 (compilation engine) after on-demand extraction.
            description = item.get("description", "")
            summary = summarize(description) if description else ""

            if dry_run:
                print(f"    [dry-run] Would store: {title[:60]} (score={score:.2f})")
                stats["stored"] += 1
                continue

            brief = {
                "source_id": source["id"],
                "title": title,
                "url": link,
                "summary": summary,
                # No content yet — extraction is deferred
                "content": None,
                "content_status": "pending",
                "author": author,
                "platform": source.get("platform") or "",
                "layer": source.get("layer") or 5,
                "category": source.get("category") or "news",
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
    print(f"\n[feed-ingest] Done in {elapsed:.2f}s")
    print(f"  Sources:    {stats['sources']}")
    print(f"  Fetched:    {stats['fetched']} items")
    print(f"  New:        {stats['new']} (dupes skipped: {stats['duplicates']})")
    print(f"  Kept:       {stats['scored_above_threshold']} above threshold")
    print(f"  Stored:     {stats['stored']}  (content_status='pending')")
    if stats["errors"]:
        print(f"  Errors:     {stats['errors']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="AOS intelligence ingest engine")
    parser.add_argument("--db", help="Path to qareen.db", default=None)
    parser.add_argument("--rsshub", help="RSSHub base URL", default=DEFAULT_RSSHUB)
    parser.add_argument("--dry-run", action="store_true", help="Score and log but don't store")
    # --no-extract is accepted for backwards compat with existing cron invocations,
    # but it's now the default (and only) behavior.
    parser.add_argument("--no-extract", action="store_true", help="(deprecated, always on)")
    args = parser.parse_args()

    asyncio.run(run_ingest(
        db_path=args.db,
        rsshub_base=args.rsshub,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
