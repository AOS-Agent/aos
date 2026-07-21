"""Digest: direction-correct sections, privacy exclusion, line bound, surfacing."""
from __future__ import annotations

import sqlite3

from core.engine.comms.ambient import digest as D

from ._helpers import entity, make_comms_db, make_people_db, msg


def _db(tmp_path):
    messages = [
        # operator promised something (outbound)
        msg("m1", "I'll send the docs", ts="2026-07-18T10:00:00",
            direction="outbound", person_id="p1"),
        # other promised operator (inbound)
        msg("m2", "I'll process your order", ts="2026-07-18T11:00:00",
            direction="inbound", person_id="p1"),
        # inbound question, never answered (latest inbound, no later outbound reply)
        msg("m3", "what time works?", ts="2026-07-20T12:00:00",
            direction="inbound", person_id="p1"),
        # transaction msg, recent
        msg("m4", "paid Acme $50", ts="2026-07-19T09:00:00",
            direction="outbound", person_id="p1"),
        # a private contact's outbound commitment — must be excluded
        msg("m5", "I'll call the bank", ts="2026-07-18T13:00:00",
            direction="outbound", person_id="p2"),
    ]
    entities = [
        entity("e1", "commitment", fields={"who": None, "what": "send the docs"},
               source_ids=["m1"], person_id="p1"),
        entity("e2", "commitment", fields={"who": None, "what": "process the order"},
               source_ids=["m2"], person_id="p1"),
        entity("e3", "question_open", fields={"value": "what time works?"},
               source_ids=["m3"], person_id="p1"),
        entity("e4", "transaction", fields={"merchant": "Acme", "amount": "$50"},
               source_ids=["m4"], person_id="p1"),
        entity("e5", "commitment", fields={"what": "call the bank"},
               source_ids=["m5"], person_id="p2"),
    ]
    comms = make_comms_db(tmp_path / "comms.db", messages, entities)
    people = make_people_db(tmp_path / "people.db", people=[
        {"id": "p1", "canonical_name": "Bilal", "privacy_level": 1},
        {"id": "p2", "canonical_name": "Banker", "privacy_level": 3},  # private
    ])
    return comms, people


def test_sections_and_direction(tmp_path):
    comms, people = _db(tmp_path)
    conn = D._connect(comms, people)
    try:
        by = D.owed_by_you(conn, limit=None)
        to = D.owed_to_you(conn, limit=None)
        qs = D.unanswered_questions(conn, limit=None)
        tx = D.recent_transactions(conn)
    finally:
        conn.close()
    # operator's own commitment (outbound) shows in "owed by you", not owed-to
    assert any("send the docs" in c["what"] for c in by)
    assert not any("process" in c["what"] for c in by)
    # other's commitment (inbound) shows in "owed to you"
    assert any("process" in c["what"] for c in to)
    assert qs and "what time" in qs[0]["q"]
    assert tx["count"] == 1 and "Acme" in tx["merchants"]


def test_privacy_excluded(tmp_path):
    comms, people = _db(tmp_path)
    conn = D._connect(comms, people)
    try:
        by = D.owed_by_you(conn, limit=None)
    finally:
        conn.close()
    # p2 is privacy_level 3 → "call the bank" must never appear
    assert not any("bank" in c["what"] for c in by)
    # …unless the operator explicitly overrides
    conn = D._connect(comms, people)
    try:
        by_priv = D.owed_by_you(conn, limit=None, include_private=True)
    finally:
        conn.close()
    assert any("bank" in c["what"] for c in by_priv)


def test_digest_line_bound(tmp_path):
    comms, people = _db(tmp_path)
    text = D.build_digest(comms, people, surface_nudges=False)
    assert text
    assert len(text.splitlines()) <= D.MAX_DIGEST_LINES


def test_empty_digest_returns_blank(tmp_path):
    comms = make_comms_db(tmp_path / "comms.db", [], [])
    people = make_people_db(tmp_path / "people.db", people=[])
    assert D.build_digest(comms, people, surface_nudges=False) == ""


def test_missing_comms_db_is_safe(tmp_path):
    assert D.build_digest(tmp_path / "nope.db", tmp_path / "nope2.db") == ""


def test_mark_surfaced_idempotent(tmp_path):
    people = make_people_db(tmp_path / "people.db",
                            people=[{"id": "p1", "canonical_name": "Bilal"}],
                            nudges=[{"id": "iq1", "person_id": "p1",
                                     "surface_type": "drift", "content": "x"}])
    assert D.mark_surfaced(["iq1"], people) == 1
    # already surfaced → not re-marked
    assert D.mark_surfaced(["iq1"], people) == 0
    conn = sqlite3.connect(people)
    got = conn.execute("SELECT surfaced_at FROM intelligence_queue WHERE id='iq1'").fetchone()[0]
    conn.close()
    assert got is not None
