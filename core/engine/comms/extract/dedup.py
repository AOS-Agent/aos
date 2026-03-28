#!/usr/bin/env python3
"""Dedup pass — merge duplicate people entries.

Finds people with identical canonical_names, picks the one with more
identifiers as primary, merges the other's identifiers/interactions
into it, and archives the duplicate.

Usage:
    python3 dedup.py              # run dedup
    python3 dedup.py --dry-run    # show what would merge
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
sys.path.insert(0, str(_PEOPLE_SERVICE))

import db as people_db

log = logging.getLogger(__name__)


def run_dedup(dry_run: bool = False) -> dict:
    """Find and merge duplicate people entries."""
    conn = people_db.connect()
    ts = people_db.now_ts()

    # Find exact-name duplicates
    dupes = conn.execute("""
        SELECT canonical_name, GROUP_CONCAT(id) as ids, COUNT(*) as cnt
        FROM people WHERE is_archived = 0
        GROUP BY canonical_name HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()

    merged = 0
    for dupe in dupes:
        ids = dupe["ids"].split(",")
        name = dupe["canonical_name"]

        # Pick primary: most identifiers, then most interactions
        best_id = None
        best_score = -1
        for pid in ids:
            ident_count = conn.execute(
                "SELECT COUNT(*) FROM person_identifiers WHERE person_id = ?", (pid,)
            ).fetchone()[0]
            ix_count = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE person_id = ?", (pid,)
            ).fetchone()[0]
            score = ident_count * 10 + ix_count
            if score > best_score:
                best_score = score
                best_id = pid

        # Merge others into best
        others = [pid for pid in ids if pid != best_id]

        if dry_run:
            log.info(f"  Would merge {name}: keep {best_id}, archive {others}")
            merged += len(others)
            continue

        for other_id in others:
            # Move identifiers (ignore conflicts)
            conn.execute(
                "UPDATE OR IGNORE person_identifiers SET person_id = ? WHERE person_id = ?",
                (best_id, other_id),
            )
            # Move interactions
            conn.execute(
                "UPDATE interactions SET person_id = ? WHERE person_id = ?",
                (best_id, other_id),
            )
            # Move aliases
            conn.execute(
                "UPDATE OR IGNORE aliases SET person_id = ? WHERE person_id = ?",
                (best_id, other_id),
            )
            # Move relationship_state (keep best's if exists)
            existing_state = conn.execute(
                "SELECT 1 FROM relationship_state WHERE person_id = ?", (best_id,)
            ).fetchone()
            if not existing_state:
                conn.execute(
                    "UPDATE relationship_state SET person_id = ? WHERE person_id = ?",
                    (best_id, other_id),
                )
            else:
                conn.execute(
                    "DELETE FROM relationship_state WHERE person_id = ?", (other_id,)
                )
            # Move patterns
            existing_pat = conn.execute(
                "SELECT 1 FROM communication_patterns WHERE person_id = ?", (best_id,)
            ).fetchone()
            if not existing_pat:
                conn.execute(
                    "UPDATE communication_patterns SET person_id = ? WHERE person_id = ?",
                    (best_id, other_id),
                )
            else:
                conn.execute(
                    "DELETE FROM communication_patterns WHERE person_id = ?", (other_id,)
                )

            # Archive the duplicate
            conn.execute(
                "UPDATE people SET is_archived = 1, updated_at = ? WHERE id = ?",
                (ts, other_id),
            )

            # Log to dedup_log
            import random, string
            did = "dd_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            conn.execute(
                "INSERT INTO dedup_log (id, action, primary_id, secondary_id, reason, confidence, decided_at, decided_by) "
                "VALUES (?, 'merge', ?, ?, 'exact_name_match', 1.0, ?, 'auto')",
                (did, best_id, other_id, ts),
            )

            merged += 1
            log.info(f"  Merged {name}: {other_id} → {best_id}")

    if not dry_run:
        conn.commit()

    conn.close()
    return {"duplicates_found": len(dupes), "entries_merged": merged, "dry_run": dry_run}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_dedup(dry_run=args.dry_run)
    print(f"\nDedup: {result['duplicates_found']} duplicate sets, {result['entries_merged']} merged")
