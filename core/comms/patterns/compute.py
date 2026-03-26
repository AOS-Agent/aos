#!/usr/bin/env python3
"""Communication pattern computation.

Analyzes interactions to compute per-person communication patterns,
auto-classify importance tiers, and filter spam/irrelevant contacts.

Three jobs in one pass:
1. Patterns — response time baselines, preferred hours, message style
2. Importance — auto-tier contacts based on interaction data
3. Filtering — flag transactional/spam contacts

Usage:
    python3 compute.py                # compute all
    python3 compute.py --dry-run      # show what would change
    python3 compute.py --person-id X  # compute for one person
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# People DB access
_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
sys.path.insert(0, str(_PEOPLE_SERVICE))

import db as people_db

log = logging.getLogger(__name__)


# ── Pattern Computation ──────────────────────────────────

def compute_patterns(conn, person_id: str) -> dict | None:
    """Compute communication patterns for a single person.

    Returns dict of pattern fields, or None if insufficient data.
    Requires >= 5 interactions to produce meaningful patterns.
    """
    interactions = conn.execute(
        "SELECT occurred_at, direction, msg_count, channel "
        "FROM interactions WHERE person_id = ? ORDER BY occurred_at",
        (person_id,),
    ).fetchall()

    if len(interactions) < 5:
        return None

    ts = people_db.now_ts()

    # Response time estimation
    # For each inbound interaction, find the next outbound within 24h
    response_times = []
    inbound_times = []
    outbound_times = []

    for ix in interactions:
        if ix["direction"] in ("inbound", "both"):
            inbound_times.append(ix["occurred_at"])
        if ix["direction"] in ("outbound", "both"):
            outbound_times.append(ix["occurred_at"])

    # Match inbound → next outbound
    out_idx = 0
    for in_ts in inbound_times:
        while out_idx < len(outbound_times) and outbound_times[out_idx] <= in_ts:
            out_idx += 1
        if out_idx < len(outbound_times):
            delta_mins = (outbound_times[out_idx] - in_ts) / 60
            if 0 < delta_mins < 1440:  # within 24h
                response_times.append(delta_mins)

    avg_response = statistics.mean(response_times) if response_times else None
    p50_response = statistics.median(response_times) if response_times else None
    p90_response = (
        sorted(response_times)[int(len(response_times) * 0.9)]
        if len(response_times) >= 5 else None
    )

    # Preferred hours and days
    hours = Counter()
    days = Counter()
    msg_lengths = []

    for ix in interactions:
        dt = datetime.fromtimestamp(ix["occurred_at"])
        hours[dt.hour] += 1
        days[dt.weekday()] += 1
        # Estimate message length from msg_count (proxy)
        count = ix["msg_count"] or 1
        msg_lengths.append(count)

    # Top 5 preferred hours
    preferred_hours = [h for h, _ in hours.most_common(5)]
    preferred_days = [d for d, _ in days.most_common(3)]

    # Style metrics
    total_msgs = sum(msg_lengths)
    avg_msg_length = statistics.mean(msg_lengths) if msg_lengths else 0
    brief_count = sum(1 for m in msg_lengths if m <= 2)  # 1-2 messages = brief
    brief_ratio = brief_count / len(msg_lengths) if msg_lengths else 0

    return {
        "person_id": person_id,
        "avg_response_time_mins": round(avg_response, 1) if avg_response else None,
        "p50_response_mins": round(p50_response, 1) if p50_response else None,
        "p90_response_mins": round(p90_response, 1) if p90_response else None,
        "preferred_hours": json.dumps(preferred_hours),
        "preferred_days": json.dumps(preferred_days),
        "style_brief_ratio": round(brief_ratio, 2),
        "avg_message_length": round(avg_msg_length, 1),
        "language": None,  # TODO: detect from message content in future
        "sample_size": len(interactions),
        "computed_at": ts,
    }


# ── Importance Classification ────────────────────────────

def classify_importance(conn, person_id: str) -> int | None:
    """Auto-classify importance tier from interaction data.

    Tiers:
      1 (inner circle): bidirectional, frequent (>= 10 interactions/90d), operator initiates
      2 (active): regular communication (>= 3 interactions/90d), mixed direction
      3 (acquaintance): occasional contact (>= 1 interaction/90d)
      4 (peripheral): no outbound ever, or only transactional

    Returns importance int, or None if insufficient data to change.
    """
    state = conn.execute(
        "SELECT interaction_count_90d, outbound_30d, inbound_30d, "
        "       msg_count_30d, trajectory "
        "FROM relationship_state WHERE person_id = ?",
        (person_id,),
    ).fetchone()

    if not state:
        return None

    count_90d = state["interaction_count_90d"] or 0
    outbound = state["outbound_30d"] or 0
    inbound = state["inbound_30d"] or 0
    total_msgs = state["msg_count_30d"] or 0

    # Check for transactional patterns
    if is_transactional(conn, person_id):
        return 4

    # Tier 1: Inner circle
    # Frequent + bidirectional + operator initiates at least 30% of the time
    if count_90d >= 10 and outbound > 0 and inbound > 0:
        outbound_ratio = outbound / max(1, outbound + inbound)
        if outbound_ratio >= 0.3:
            return 1

    # Tier 2: Active
    # Regular communication, at least some outbound
    if count_90d >= 3 and (outbound > 0 or total_msgs >= 10):
        return 2

    # Tier 3: Acquaintance
    if count_90d >= 1:
        return 3

    # Tier 4: Peripheral
    return 4


def is_transactional(conn, person_id: str) -> bool:
    """Detect transactional/spam contacts.

    Signals:
    - Zero outbound messages ever
    - Person name looks like a business (contains common business patterns)
    - Very short interaction history (1-2 interactions, all inbound)
    """
    # Check outbound
    outbound_ever = conn.execute(
        "SELECT COUNT(*) FROM interactions "
        "WHERE person_id = ? AND direction IN ('outbound', 'both')",
        (person_id,),
    ).fetchone()[0]

    if outbound_ever > 0:
        return False  # Operator engages = not spam

    # Check total interaction count
    total = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE person_id = ?",
        (person_id,),
    ).fetchone()[0]

    if total <= 2:
        return True  # 1-2 inbound-only = likely transactional

    # Check name patterns
    person = conn.execute(
        "SELECT canonical_name, display_name FROM people WHERE id = ?",
        (person_id,),
    ).fetchone()

    if person:
        name = (person["canonical_name"] or "").lower()
        business_patterns = [
            "shop", "store", "pizza", "food", "driver", "delivery",
            "service", "clinic", "hospital", "bank", "insurance",
            "plumber", "electrician", "maid", "cleaner",
        ]
        if any(p in name for p in business_patterns):
            return True

    return False


# ── Main Compute ─────────────────────────────────────────

def run_compute(person_ids: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run full pattern computation + importance classification.

    Args:
        person_ids: Specific people to compute (None = all with interactions)
        dry_run: Show what would change without writing

    Returns:
        Summary dict
    """
    conn = people_db.connect()
    ts = people_db.now_ts()

    # Get people with interactions
    if person_ids:
        placeholders = ",".join("?" * len(person_ids))
        rows = conn.execute(
            f"SELECT DISTINCT person_id FROM interactions WHERE person_id IN ({placeholders})",
            person_ids,
        ).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT person_id FROM interactions").fetchall()

    all_pids = [r[0] for r in rows]
    log.info(f"Computing patterns for {len(all_pids)} people...")

    patterns_written = 0
    importance_changed = 0
    transactional_flagged = 0

    for pid in all_pids:
        # 1. Compute patterns
        pattern = compute_patterns(conn, pid)
        if pattern:
            if not dry_run:
                # Upsert
                existing = conn.execute(
                    "SELECT person_id FROM communication_patterns WHERE person_id = ?",
                    (pid,),
                ).fetchone()
                if existing:
                    sets = ", ".join(f"{k} = ?" for k in pattern if k != "person_id")
                    vals = [v for k, v in pattern.items() if k != "person_id"] + [pid]
                    conn.execute(
                        f"UPDATE communication_patterns SET {sets} WHERE person_id = ?",
                        vals,
                    )
                else:
                    cols = ", ".join(pattern.keys())
                    placeholders = ", ".join("?" * len(pattern))
                    conn.execute(
                        f"INSERT INTO communication_patterns ({cols}) VALUES ({placeholders})",
                        list(pattern.values()),
                    )
            patterns_written += 1

        # 2. Classify importance
        new_importance = classify_importance(conn, pid)
        if new_importance is not None:
            current = conn.execute(
                "SELECT importance FROM people WHERE id = ?", (pid,)
            ).fetchone()
            if current:
                old_importance = current["importance"]
                # Only auto-upgrade to tier 1-2, never auto-downgrade inner circle
                # Manual importance <= 2 is never overridden downward
                should_update = False
                if old_importance == 3 and new_importance <= 2:
                    should_update = True  # Promote acquaintance to active/inner
                elif old_importance >= 3 and new_importance != old_importance:
                    should_update = True  # Reclassify acquaintance/peripheral
                elif old_importance == 5 and new_importance <= 4:
                    should_update = True  # Default (5) → anything

                if should_update:
                    if not dry_run:
                        conn.execute(
                            "UPDATE people SET importance = ?, updated_at = ? WHERE id = ?",
                            (new_importance, ts, pid),
                        )
                    importance_changed += 1
                    if new_importance == 4 and is_transactional(conn, pid):
                        transactional_flagged += 1

    if not dry_run:
        conn.commit()

    conn.close()

    summary = {
        "people_processed": len(all_pids),
        "patterns_written": patterns_written,
        "importance_changed": importance_changed,
        "transactional_flagged": transactional_flagged,
        "dry_run": dry_run,
    }

    log.info(f"  Patterns: {patterns_written} written")
    log.info(f"  Importance: {importance_changed} reclassified")
    log.info(f"  Transactional: {transactional_flagged} flagged")

    return summary


# ── CLI ──────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Compute communication patterns + importance tiers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--person-id", type=str, help="Compute for a specific person")
    args = parser.parse_args()

    pids = [args.person_id] if args.person_id else None
    result = run_compute(person_ids=pids, dry_run=args.dry_run)

    print()
    print("═" * 50)
    print(f"  Pattern Compute {'(DRY RUN)' if result['dry_run'] else ''}")
    print("═" * 50)
    print(f"  People processed:     {result['people_processed']}")
    print(f"  Patterns written:     {result['patterns_written']}")
    print(f"  Importance changed:   {result['importance_changed']}")
    print(f"  Transactional flagged: {result['transactional_flagged']}")
    print("═" * 50)


if __name__ == "__main__":
    main()
