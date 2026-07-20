"""Ontology lift: mapping, candidate selection, deferred (no-writer) behavior."""
from __future__ import annotations

import json
import sqlite3

from core.engine.comms.enrich.lift import (
    lift_payload,
    lift_pending,
    surfaceable_candidates,
)

from ._helpers import make_comms_db


def _entity(conn, eid, etype, fields, conf, *, status="active", ontype=None, sids=("m0",)):
    conn.execute(
        "INSERT INTO message_entities(id, entity_type, value, fields_json, confidence,"
        " source_ids, person_id, batch_key, extractor_version, model, created_at,"
        " ontology_type, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, etype, str(fields.get("value") or fields.get("merchant") or fields.get("what") or ""),
         json.dumps(fields), conf, json.dumps(list(sids)), "p1", "b", "extract@1", "m",
         "2026-03-09T10:00:00", ontype, status))
    conn.commit()


def test_mapping_transaction_and_commitment():
    tp = lift_payload({"id": "e1", "entity_type": "transaction",
                       "fields_json": json.dumps({"merchant": "Costco", "amount": "40"}),
                       "source_ids": json.dumps(["m0"]), "confidence": 0.9, "person_id": "p1"})
    assert tp.ontology_type == "transaction" and tp.fields["merchant"] == "Costco"
    cp = lift_payload({"id": "e2", "entity_type": "commitment",
                       "fields_json": json.dumps({"who": "p1", "what": "drop order"}),
                       "source_ids": json.dumps(["m0"]), "confidence": 0.9, "person_id": "p1"})
    assert cp.ontology_type == "reminder"


def test_topic_and_mention_do_not_lift():
    assert lift_payload({"id": "e", "entity_type": "topic",
                         "fields_json": "{}", "source_ids": "[]", "confidence": 0.9}) is None
    assert lift_payload({"id": "e", "entity_type": "mention",
                         "fields_json": "{}", "source_ids": "[]", "confidence": 0.9}) is None


def test_candidates_respect_threshold_and_status(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    conn = sqlite3.connect(db)
    _entity(conn, "hi", "transaction", {"merchant": "Costco"}, 0.90)          # candidate
    _entity(conn, "lo", "transaction", {"merchant": "x"}, 0.70)               # below surface_min
    _entity(conn, "done", "transaction", {"merchant": "y"}, 0.95, ontype="transaction")  # already lifted
    _entity(conn, "sup", "commitment", {"what": "z"}, 0.95, status="superseded")  # not active
    cands = surfaceable_candidates(conn, surface_min=0.80)
    assert {c["id"] for c in cands} == {"hi"}
    conn.close()


def test_lift_pending_without_writer_defers(tmp_path):
    # No writer today → payloads produced, but entity rows stay unstamped so the
    # real lift is a no-loss re-run once the ontology store lands (Phase 5).
    db = make_comms_db(tmp_path / "comms.db")
    conn = sqlite3.connect(db)
    _entity(conn, "e1", "transaction", {"merchant": "Costco"}, 0.9)
    payloads = lift_pending(conn, surface_min=0.80)
    assert len(payloads) == 1 and payloads[0].ontology_type == "transaction"
    still_null = conn.execute(
        "SELECT ontology_type FROM message_entities WHERE id='e1'").fetchone()[0]
    assert still_null is None  # deferred, nothing consumed
    conn.close()


def test_lift_pending_with_writer_stamps(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    conn = sqlite3.connect(db)
    _entity(conn, "e1", "commitment", {"what": "drop order"}, 0.9)
    payloads = lift_pending(conn, surface_min=0.80, writer=lambda p: "reminder_123")
    assert len(payloads) == 1
    row = conn.execute(
        "SELECT ontology_type, ontology_id FROM message_entities WHERE id='e1'").fetchone()
    assert row == ("reminder", "reminder_123")
    conn.close()
