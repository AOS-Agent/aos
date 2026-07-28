"""Digest: direction-correct sections, privacy exclusion, line bound, surfacing."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from core.engine.comms.ambient import digest as D

from ._helpers import entity, make_comms_db, make_people_db, msg


def _ago(days: float) -> str:
    """A timestamp `days` before now.

    Fixture timestamps must be relative. Every digest window is measured
    against the wall clock (TX_WINDOW_DAYS=7, STALE_UNDATED_DAYS=14,
    QUESTION_WINDOW_DAYS=21), so absolute dates silently age out of range and
    turn the suite red on a calendar date rather than on a code change.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S")


def _db(tmp_path):
    # m3 must remain the newest message for p1: an "unanswered" question
    # requires that no outbound to that person follows it.
    messages = [
        # operator promised something (outbound)
        msg("m1", "I'll send the docs", ts=_ago(4),
            direction="outbound", person_id="p1"),
        # other promised operator (inbound)
        msg("m2", "I'll process your order", ts=_ago(3),
            direction="inbound", person_id="p1"),
        # inbound question, never answered (latest inbound, no later outbound reply)
        msg("m3", "what time works?", ts=_ago(1),
            direction="inbound", person_id="p1"),
        # transaction msg, recent
        msg("m4", "paid Acme $50", ts=_ago(2),
            direction="outbound", person_id="p1"),
        # a private contact's outbound commitment — must be excluded
        msg("m5", "I'll call the bank", ts=_ago(3),
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
