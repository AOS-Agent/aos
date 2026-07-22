"""Comms-entities sensor — daily aggregate of what enrichment surfaced.

Reads message_entities rows created in the last window and writes ONE
signal per entity_type that crossed its notability floor. Signals are
TAINTED (derived from external message content — council decision 8:
externally-derived facts carry the taint flag into every consumer).

Deterministic SQL only; no LLM. Provenance = the contributing entity ids.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import signals

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Per-type floors: below this daily count, the aggregate isn't a signal.
# question_open/topic are high-volume noise; commitments always matter.
_FLOORS = {
    "commitment": 1,
    "transaction": 1,
    "event": 5,
    "question_open": 10,
    "mention": 25,
    "topic": None,  # never signal raw topic volume
}

_MAX_REFS = 50  # provenance sample cap per signal


def run(window_hours: int = 24, db_path: Path | None = None) -> list[str]:
    """Scan the window, write aggregate signals. Returns new signal ids."""
    db = db_path or COMMS_DB
    if not Path(db).exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT entity_type, id, value, person_id
            FROM message_entities
            WHERE created_at > datetime('now', ?)
              AND status = 'active'
            ORDER BY created_at DESC
            """,
            (f"-{int(window_hours)} hours",),
        ).fetchall()
    finally:
        conn.close()

    by_type: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_type.setdefault(r["entity_type"], []).append(r)

    written: list[str] = []
    for etype, group in sorted(by_type.items()):
        floor = _FLOORS.get(etype)
        if floor is None or len(group) < floor:
            continue
        people = sorted({r["person_id"] for r in group if r["person_id"]})
        sample = [
            {"value": (r["value"] or "")[:120], "person_id": r["person_id"]}
            for r in group[:5]
        ]
        written.append(
            signals.append_signal(
                sensor="comms_entities",
                signal_type=f"daily_{etype}",
                payload={
                    "count": len(group),
                    "window_hours": window_hours,
                    "distinct_people": len(people),
                    "sample": sample,
                },
                source_refs=[r["id"] for r in group[:_MAX_REFS]],
                tainted=True,  # external message content — always
            )
        )
    return written
