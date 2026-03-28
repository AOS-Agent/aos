"""Pattern query helpers.

Simple read-only functions for consumers (graduation engine, draft engine,
dashboard) to access communication patterns and relationship state.

Usage:
    from core.comms.patterns.query import get_pattern, get_top_contacts

    pattern = get_pattern(person_id, conn)
    top = get_top_contacts(conn, n=20)
"""

from __future__ import annotations

import json
import sqlite3


def get_pattern(person_id: str, conn: sqlite3.Connection) -> dict | None:
    """Get communication patterns for a person.

    Returns:
        Dict with pattern fields, or None if not computed.
    """
    row = conn.execute(
        "SELECT * FROM communication_patterns WHERE person_id = ?",
        (person_id,),
    ).fetchone()

    if not row:
        return None

    result = dict(row)
    # Parse JSON fields
    for field in ("preferred_hours", "preferred_days"):
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, TypeError):
                result[field] = []
    return result


def get_top_contacts(conn: sqlite3.Connection, n: int = 20) -> list[dict]:
    """Get top N contacts by message frequency.

    Returns list of dicts with person info + pattern + state.
    """
    rows = conn.execute("""
        SELECT
            p.id, p.canonical_name, p.importance, p.nickname,
            rs.msg_count_30d, rs.trajectory, rs.days_since_contact,
            rs.interaction_count_90d, rs.outbound_30d, rs.inbound_30d,
            cp.avg_response_time_mins, cp.p50_response_mins,
            cp.sample_size
        FROM people p
        JOIN relationship_state rs ON rs.person_id = p.id
        LEFT JOIN communication_patterns cp ON cp.person_id = p.id
        WHERE p.is_archived = 0 AND rs.msg_count_30d > 0
        ORDER BY rs.msg_count_30d DESC
        LIMIT ?
    """, (n,)).fetchall()

    return [dict(r) for r in rows]


def get_person_summary(person_id: str, conn: sqlite3.Connection) -> dict | None:
    """Get a full person summary: profile + state + patterns.

    Used by the draft engine to assemble context.
    """
    person = conn.execute(
        "SELECT id, canonical_name, display_name, first_name, last_name, "
        "       nickname, importance FROM people WHERE id = ?",
        (person_id,),
    ).fetchone()

    if not person:
        return None

    result = dict(person)

    # Add relationship state
    state = conn.execute(
        "SELECT * FROM relationship_state WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    if state:
        result["state"] = dict(state)

    # Add patterns
    result["patterns"] = get_pattern(person_id, conn)

    # Add metadata
    meta = conn.execute(
        "SELECT organization, preferred_channel, city, country "
        "FROM contact_metadata WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    if meta:
        result["metadata"] = dict(meta)

    return result
