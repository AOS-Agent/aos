#!/usr/bin/env python3
"""Unified retroactive extraction pipeline.

Uses the existing channel adapters to extract message history, resolves
senders to person_ids, and writes interaction rows to the People DB.
One pipeline, any adapter. Adding a new channel = zero extraction work.

Usage:
    python3 pipeline.py                    # all adapters, last 365 days
    python3 pipeline.py --days 90          # last 90 days
    python3 pipeline.py --channel imessage # iMessage only
    python3 pipeline.py --dry-run          # show what would be written
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# People DB access
_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
sys.path.insert(0, str(_PEOPLE_SERVICE))

import db as people_db

# Adapter access
_AOS_ROOT = Path.home() / "aos"
sys.path.insert(0, str(_AOS_ROOT))

log = logging.getLogger(__name__)


# ── Resolver cache ───────────────────────────────────────

class ResolverCache:
    """Batch-resolve handles to person_ids with caching.

    Resolves each unique handle once, caches the result for the session.
    Much faster than resolving per-message.
    """

    def __init__(self, conn):
        self._conn = conn
        self._cache: dict[str, str | None] = {}
        self._resolve_fn = None

    def _get_resolver(self):
        """Lazy-load the resolver (it imports db module)."""
        if self._resolve_fn is None:
            from resolver import resolve_contact
            self._resolve_fn = resolve_contact
        return self._resolve_fn

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone to digits only."""
        import re
        return re.sub(r"[^\d]", "", phone)

    def _try_identifier(self, handle: str) -> str | None:
        """Try direct identifier lookup in person_identifiers table.

        Handles phone numbers (+15551234567), emails, WhatsApp JIDs.
        """
        import re

        # Phone number detection
        clean = handle.strip().replace(" ", "").replace("-", "")
        if re.match(r'^\+?\d{7,15}$', clean):
            normalized = self._normalize_phone(clean)
            # Try exact normalized match
            row = self._conn.execute(
                "SELECT person_id FROM person_identifiers "
                "WHERE normalized = ? OR normalized = ? LIMIT 1",
                (normalized, "+" + normalized),
            ).fetchone()
            if row:
                return row[0]
            # Try suffix match (last 10 digits) for international number variations
            if len(normalized) >= 10:
                suffix = normalized[-10:]
                row = self._conn.execute(
                    "SELECT person_id FROM person_identifiers "
                    "WHERE normalized LIKE ? LIMIT 1",
                    (f"%{suffix}",),
                ).fetchone()
                if row:
                    return row[0]

        # Email detection
        if "@" in handle and "whatsapp" not in handle:
            row = self._conn.execute(
                "SELECT person_id FROM person_identifiers "
                "WHERE LOWER(value) = ? OR LOWER(normalized) = ? LIMIT 1",
                (handle.lower(), handle.lower()),
            ).fetchone()
            if row:
                return row[0]

        # WhatsApp JID
        if "@s.whatsapp.net" in handle:
            phone = handle.split("@")[0]
            return self._try_identifier(phone)

        return None

    def resolve(self, handle: str) -> str | None:
        """Resolve a handle to a person_id, using cache.

        Tries identifier lookup first (phone/email), then name-based resolver.
        """
        if handle in self._cache:
            return self._cache[handle]

        if not handle or handle == "me":
            self._cache[handle] = None
            return None

        # Try direct identifier lookup first (phone, email, JID)
        pid = self._try_identifier(handle)
        if pid:
            self._cache[handle] = pid
            return pid

        # Fall back to name-based resolver
        resolver = self._get_resolver()
        try:
            result = resolver(handle, conn=self._conn)
            pid = result.get("person_id") if result.get("resolved") else None
            self._cache[handle] = pid
            return pid
        except Exception:
            self._cache[handle] = None
            return None

    @property
    def stats(self) -> dict:
        total = len(self._cache)
        resolved = sum(1 for v in self._cache.values() if v is not None)
        return {"total_handles": total, "resolved": resolved, "unresolved": total - resolved}


# ── Interaction grouping ─────────────────────────────────

def _date_key(dt: datetime) -> str:
    """Calendar date key for grouping: YYYY-MM-DD."""
    return dt.strftime("%Y-%m-%d")


def _nanoid() -> str:
    """Generate a unique interaction ID."""
    import random
    import string
    chars = string.ascii_lowercase + string.digits
    return "ix_" + "".join(random.choices(chars, k=10))


def _group_messages(messages, resolver: ResolverCache) -> dict:
    """Group messages by (person_id, channel, date).

    Returns: {(person_id, channel, date_str): {"inbound": N, "outbound": N, "occurred_at": earliest_ts}}
    """
    groups: dict[tuple, dict] = {}

    for msg in messages:
        # Resolve sender
        sender = msg.sender
        if msg.from_me:
            # For outbound, we need to resolve the conversation partner
            # The conversation_id or metadata might have the handle
            handle = msg.conversation_id
            person_id = resolver.resolve(handle)
        else:
            person_id = resolver.resolve(sender)

        if not person_id:
            continue

        date_str = _date_key(msg.timestamp)
        key = (person_id, msg.channel, date_str)

        if key not in groups:
            groups[key] = {
                "inbound": 0,
                "outbound": 0,
                "occurred_at": msg.timestamp,
            }

        if msg.from_me:
            groups[key]["outbound"] += 1
        else:
            groups[key]["inbound"] += 1

        # Track earliest timestamp
        if msg.timestamp < groups[key]["occurred_at"]:
            groups[key]["occurred_at"] = msg.timestamp

    return groups


# ── Deduplication ────────────────────────────────────────

def _interaction_exists(conn, person_id: str, channel: str, occurred_at: int, window_s: int = 86400) -> bool:
    """Check if an interaction already exists within the time window."""
    row = conn.execute(
        "SELECT 1 FROM interactions WHERE person_id = ? AND channel = ? "
        "AND ABS(occurred_at - ?) < ?",
        (person_id, channel, occurred_at, window_s),
    ).fetchone()
    return row is not None


# ── Relationship state computation ───────────────────────

def compute_relationship_state(conn):
    """Recompute relationship_state for all people with interactions."""
    ts = people_db.now_ts()

    # Get all people with interactions
    rows = conn.execute("""
        SELECT
            person_id,
            COUNT(*) as total_interactions,
            MAX(occurred_at) as last_at,
            MIN(occurred_at) as first_at,
            SUM(CASE WHEN occurred_at >= ? THEN 1 ELSE 0 END) as count_7d,
            SUM(CASE WHEN occurred_at >= ? THEN 1 ELSE 0 END) as count_30d,
            SUM(CASE WHEN occurred_at >= ? THEN 1 ELSE 0 END) as count_90d,
            SUM(CASE WHEN occurred_at >= ? THEN msg_count ELSE 0 END) as msgs_30d,
            SUM(CASE WHEN direction = 'outbound' AND occurred_at >= ? THEN msg_count ELSE 0 END) as out_30d,
            SUM(CASE WHEN direction = 'inbound' AND occurred_at >= ? THEN msg_count ELSE 0 END) as in_30d
        FROM interactions
        GROUP BY person_id
    """, (
        ts - 7 * 86400,
        ts - 30 * 86400,
        ts - 90 * 86400,
        ts - 30 * 86400,
        ts - 30 * 86400,
        ts - 30 * 86400,
    )).fetchall()

    updated = 0
    for row in rows:
        pid = row[0]
        total = row[1]
        last_at = row[2]
        first_at = row[3]

        # Compute average days between interactions
        if total > 1 and last_at and first_at:
            span_days = max(1, (last_at - first_at) / 86400)
            avg_days = span_days / total
        else:
            avg_days = None

        # Trajectory
        count_30d = row[5] or 0
        count_90d = row[6] or 0
        if count_90d == 0:
            trajectory = "dormant"
        elif count_30d == 0:
            trajectory = "drifting"
        elif count_30d > (count_90d / 3) * 1.5:
            trajectory = "growing"
        else:
            trajectory = "stable"

        days_since = int((ts - last_at) / 86400) if last_at else None

        people_db.set_relationship_state(conn, pid,
            last_interaction_at=last_at,
            avg_days_between=avg_days,
            interaction_count_7d=row[4] or 0,
            interaction_count_30d=count_30d,
            interaction_count_90d=count_90d,
            msg_count_30d=row[7] or 0,
            outbound_30d=row[8] or 0,
            inbound_30d=row[9] or 0,
            days_since_contact=days_since,
            trajectory=trajectory,
        )
        updated += 1

    conn.commit()
    return updated


# ── Main pipeline ────────────────────────────────────────

def extract_channel(adapter, days: int, conn, resolver: ResolverCache, dry_run: bool = False) -> dict:
    """Extract history from a single adapter.

    Returns stats dict.
    """
    channel_name = adapter.name
    since = datetime.now() - timedelta(days=days)

    log.info(f"  [{channel_name}] Fetching messages since {since.date()}...")

    if not adapter.is_available():
        log.warning(f"  [{channel_name}] Not available, skipping")
        return {"channel": channel_name, "status": "unavailable", "messages": 0, "interactions": 0}

    try:
        messages = adapter.get_messages(since=since)
    except Exception as e:
        log.error(f"  [{channel_name}] Failed to fetch messages: {e}")
        return {"channel": channel_name, "status": "error", "error": str(e), "messages": 0, "interactions": 0}

    if not messages:
        log.info(f"  [{channel_name}] No messages found")
        return {"channel": channel_name, "status": "ok", "messages": 0, "interactions": 0}

    log.info(f"  [{channel_name}] {len(messages)} messages fetched, grouping...")

    # Group messages
    groups = _group_messages(messages, resolver)

    # Write interactions
    written = 0
    skipped = 0
    for (person_id, channel, date_str), data in groups.items():
        occurred_at = int(data["occurred_at"].timestamp())

        # Deduplicate
        if _interaction_exists(conn, person_id, channel, occurred_at):
            skipped += 1
            continue

        if dry_run:
            written += 1
            continue

        # Determine direction
        if data["inbound"] > 0 and data["outbound"] > 0:
            direction = "both"
        elif data["outbound"] > 0:
            direction = "outbound"
        else:
            direction = "inbound"

        conn.execute(
            "INSERT OR IGNORE INTO interactions "
            "(id, person_id, occurred_at, channel, direction, msg_count, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_nanoid(), person_id, occurred_at, channel, direction,
             data["inbound"] + data["outbound"], people_db.now_ts()),
        )
        written += 1

    if not dry_run:
        conn.commit()

    log.info(f"  [{channel_name}] {written} interactions written, {skipped} deduplicated")
    return {
        "channel": channel_name,
        "status": "ok",
        "messages": len(messages),
        "interactions": written,
        "skipped": skipped,
    }


def run_extraction(days: int = 365, channels: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run the full extraction pipeline across all available adapters.

    Args:
        days: How far back to extract (default 365)
        channels: Filter to specific channels (default: all)
        dry_run: Show what would be written without writing

    Returns:
        Summary dict with per-channel stats
    """
    conn = people_db.connect()
    resolver = ResolverCache(conn)

    # Load adapters
    try:
        from core.comms.registry import load_adapters
        adapters = load_adapters()
    except ImportError:
        # Fallback: load individually
        adapters = []
        try:
            from core.comms.channels.imessage import iMessageAdapter
            adapters.append(iMessageAdapter())
        except Exception:
            pass
        try:
            from core.comms.channels.whatsapp import WhatsAppAdapter
            adapters.append(WhatsAppAdapter())
        except Exception:
            pass

    if channels:
        adapters = [a for a in adapters if a.name in channels]

    if not adapters:
        log.warning("No adapters available")
        return {"status": "no_adapters", "channels": []}

    log.info(f"Extracting {days} days of history from {len(adapters)} channels...")
    if dry_run:
        log.info("  (DRY RUN — no data will be written)")

    results = []
    for adapter in adapters:
        result = extract_channel(adapter, days, conn, resolver, dry_run)
        results.append(result)

    # Compute relationship state from all interactions
    if not dry_run:
        log.info("Recomputing relationship state...")
        state_count = compute_relationship_state(conn)
        log.info(f"  Updated state for {state_count} people")
    else:
        state_count = 0

    conn.close()

    # Summary
    total_messages = sum(r.get("messages", 0) for r in results)
    total_interactions = sum(r.get("interactions", 0) for r in results)

    summary = {
        "status": "ok",
        "days": days,
        "dry_run": dry_run,
        "total_messages": total_messages,
        "total_interactions": total_interactions,
        "state_updates": state_count,
        "resolver_stats": resolver.stats,
        "channels": results,
    }

    return summary


# ── CLI ──────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    parser = argparse.ArgumentParser(description="Retroactive extraction pipeline")
    parser.add_argument("--days", type=int, default=365, help="Days of history to extract (default: 365)")
    parser.add_argument("--channel", type=str, help="Extract from a specific channel only")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written")
    args = parser.parse_args()

    channels = [args.channel] if args.channel else None
    result = run_extraction(days=args.days, channels=channels, dry_run=args.dry_run)

    # Print summary
    print()
    print("═" * 50)
    print(f"  Extraction Summary {'(DRY RUN)' if result.get('dry_run') else ''}")
    print("═" * 50)
    print(f"  Window:         {result['days']} days")
    print(f"  Messages read:  {result['total_messages']}")
    print(f"  Interactions:   {result['total_interactions']}")
    print(f"  State updates:  {result['state_updates']}")

    rs = result.get("resolver_stats", {})
    print(f"  Handles seen:   {rs.get('total_handles', 0)} ({rs.get('resolved', 0)} resolved)")
    print()

    for ch in result.get("channels", []):
        status = "✅" if ch["status"] == "ok" else "⚠️" if ch["status"] == "unavailable" else "❌"
        print(f"  {status} {ch['channel']:12s}  {ch.get('messages', 0)} msgs → {ch.get('interactions', 0)} interactions")

    print("═" * 50)


if __name__ == "__main__":
    main()
