"""Pattern update consumer.

Receives messages from the bus and incrementally updates communication
patterns for people whose sample_size has grown enough to warrant
recomputation. Avoids thrashing — only recomputes when >= 5 new
interactions have been logged since last compute.

Registered alongside PeopleIntelConsumer in the bus.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from pathlib import Path

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
_RECOMPUTE_THRESHOLD = 5  # Only recompute after 5+ new interactions


def _get_people_db():
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        return people_db
    except ImportError:
        return None


def _get_resolver():
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import resolver
        return resolver
    except ImportError:
        return None


class PatternUpdateConsumer(Consumer):
    """Incrementally updates communication patterns from new messages."""

    name = "pattern_update"

    def process(self, messages: list[Message]) -> int:
        """Process a batch of messages and recompute patterns where needed.

        Returns:
            Number of people whose patterns were recomputed.
        """
        if not messages:
            return 0

        people_db = _get_people_db()
        resolver_mod = _get_resolver()
        if not people_db or not resolver_mod:
            return 0

        conn = people_db.connect()

        try:
            # Resolve senders to person_ids
            person_counts: Counter = Counter()
            for msg in messages:
                if msg.from_me:
                    continue
                try:
                    result = resolver_mod.resolve_contact(msg.sender, conn=conn)
                    if result.get("resolved") and result.get("person_id"):
                        person_counts[result["person_id"]] += 1
                except Exception:
                    continue

            if not person_counts:
                return 0

            # Check which people need recomputation
            recomputed = 0
            for pid, new_count in person_counts.items():
                # Check current sample_size
                row = conn.execute(
                    "SELECT sample_size FROM communication_patterns WHERE person_id = ?",
                    (pid,),
                ).fetchone()

                current_size = row["sample_size"] if row else 0

                # Count actual interactions in DB
                actual = conn.execute(
                    "SELECT COUNT(*) FROM interactions WHERE person_id = ?",
                    (pid,),
                ).fetchone()[0]

                # Recompute if enough new data
                if actual - current_size >= _RECOMPUTE_THRESHOLD:
                    try:
                        # Import compute lazily to avoid circular imports
                        from core.comms.patterns.compute import compute_patterns
                        pattern = compute_patterns(conn, pid)
                        if pattern:
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
                            recomputed += 1
                    except Exception as e:
                        log.debug(f"Pattern recompute failed for {pid}: {e}")

            if recomputed:
                conn.commit()
                log.info(f"Updated patterns for {recomputed} people")

            return recomputed

        finally:
            conn.close()
