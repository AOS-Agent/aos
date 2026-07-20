"""Ontology lift — surfaceable entities → existing ontology types (Decision #7).

The frozen schema §4 maps each entity_type to an ontology target:

    transaction   → ObjectType.TRANSACTION
    commitment    → ObjectType.REMINDER
    event         → ObjectType.REMINDER
    mention       → LinkType.MENTIONS (message→PERSON, link only)
    question_open → REMINDER (optional open loop)
    topic         → not lifted (retrieval tag only)

Only entities at/above the surface threshold and still `status='active'` are
lift candidates. This module implements the ENTITIES-SIDE interface fully: it
selects candidates, normalizes each into a typed `LiftPayload`, and stamps the
entity row's `ontology_type` / `ontology_id` once lifted.

THE OBJECT-STORE WRITE IS A PHASE 5 SEAM — INTENTIONALLY. Decision #7 lifts into
"existing ontology types", but on main those stores do not exist yet: the work
adapter references `reminders` / `transactions` tables (adapters/work.py
`_detect_type`) that are not created in any schema. Wiring the write means
designing those tables (columns, person linkage, dedup across re-extractions) —
design decisions beyond the mapping table, which the Phase 4 brief says to defer.
So `lift_pending` takes an optional `writer(payload) -> ontology_id`; when Phase 5
provides one, the payload is created and the entity stamped. With no writer (the
default today) it produces and returns the payloads WITHOUT stamping, so the same
candidates lift cleanly once the store lands — no data is consumed prematurely.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

# entity_type → ontology_type string written onto message_entities.ontology_type.
_LIFT_MAP = {
    "transaction": "transaction",   # ObjectType.TRANSACTION
    "commitment": "reminder",       # ObjectType.REMINDER
    "event": "reminder",            # ObjectType.REMINDER (no EVENT ObjectType)
    "question_open": "reminder",    # optional open loop
    # "mention" is a LINK (MENTIONS), handled separately, not an object lift.
    # "topic" is never lifted.
}

# Implemented now (Phase 4 brief): transaction + commitment. The rest are mapped
# but left for Phase 5 to enable, to keep the first lift tight and inspectable.
_LIFT_NOW = {"transaction", "commitment"}


@dataclass
class LiftPayload:
    """A normalized, ready-to-create ontology object derived from one entity."""
    ontology_type: str              # "transaction" | "reminder"
    entity_id: str                  # source message_entities.id
    person_id: Optional[str]
    fields: dict                    # typed fields (merchant/amount… or who/what/due)
    source_ids: list[str]           # provenance → messages.id
    confidence: float


def lift_payload(entity_row: dict) -> Optional[LiftPayload]:
    """Map one entity row (dict from message_entities) to a LiftPayload, or None
    if its type doesn't lift or isn't enabled yet."""
    etype = entity_row["entity_type"]
    if etype not in _LIFT_MAP or etype not in _LIFT_NOW:
        return None
    try:
        fields = json.loads(entity_row.get("fields_json") or "{}")
    except Exception:
        fields = {}
    try:
        source_ids = json.loads(entity_row.get("source_ids") or "[]")
    except Exception:
        source_ids = []
    return LiftPayload(
        ontology_type=_LIFT_MAP[etype],
        entity_id=entity_row["id"],
        person_id=entity_row.get("person_id"),
        fields=fields,
        source_ids=source_ids,
        confidence=entity_row.get("confidence") or 0.0,
    )


def surfaceable_candidates(conn: sqlite3.Connection, *, surface_min: float) -> list[dict]:
    """Active, at/above-threshold entities of a liftable-now type that haven't
    been lifted yet (ontology_type IS NULL)."""
    placeholders = ",".join("?" for _ in _LIFT_NOW)
    rows = conn.execute(
        f"""SELECT id, entity_type, value, fields_json, confidence, source_ids,
                   person_id
              FROM message_entities
             WHERE status = 'active'
               AND confidence >= ?
               AND ontology_type IS NULL
               AND entity_type IN ({placeholders})""",
        (surface_min, *sorted(_LIFT_NOW)),
    ).fetchall()
    cols = ["id", "entity_type", "value", "fields_json", "confidence",
            "source_ids", "person_id"]
    return [dict(zip(cols, r)) for r in rows]


def mark_lifted(conn: sqlite3.Connection, entity_id: str, ontology_type: str,
                ontology_id: str) -> None:
    """Stamp the entity row once its ontology object exists."""
    conn.execute(
        "UPDATE message_entities SET ontology_type=?, ontology_id=? WHERE id=?",
        (ontology_type, ontology_id, entity_id),
    )
    conn.commit()


def lift_pending(conn: sqlite3.Connection, *, surface_min: float,
                 writer: Optional[Callable[[LiftPayload], str]] = None) -> list[LiftPayload]:
    """Build lift payloads for all surfaceable transaction/commitment entities.

    writer: Phase 5 hook — given a payload, creates the ontology object and
    returns its id; the entity is then stamped. Without a writer (today), the
    payloads are returned unstamped for inspection, and nothing is written — the
    entities remain candidates so the real lift is a no-loss re-run once the
    ontology transaction/reminder store exists.
    """
    payloads: list[LiftPayload] = []
    for row in surfaceable_candidates(conn, surface_min=surface_min):
        payload = lift_payload(row)
        if payload is None:
            continue
        payloads.append(payload)
        if writer is not None:
            ontology_id = writer(payload)
            if ontology_id:
                mark_lifted(conn, payload.entity_id, payload.ontology_type, ontology_id)
    return payloads
