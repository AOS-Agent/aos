"""Proposer: only operator (outbound) commitments, dedup, no-reproposal, dry-run."""
from __future__ import annotations

import sqlite3

from core.engine.comms.ambient import proposer as P

from ._helpers import ago, entity, make_comms_db, msg


def _db(tmp_path):
    # Relative, not absolute: the proposer drops anything past
    # STALE_UNDATED_DAYS, so pinned dates expire and fail the suite on a
    # calendar date. These three only need to be recent and durable.
    messages = [
        msg("m1", "I'll send it", ts=ago(3), direction="outbound", person_id="p1"),
        msg("m2", "I'll pay you back", ts=ago(2.9), direction="inbound", person_id="p1"),
        msg("m3", "low conf promise", ts=ago(2.8), direction="outbound", person_id="p1"),
    ]
    entities = [
        entity("e1", "commitment", fields={"what": "send it"}, source_ids=["m1"],
               person_id="p1", conf=0.9),
        # inbound → NOT the operator's, must be skipped
        entity("e2", "commitment", fields={"what": "pay back"}, source_ids=["m2"],
               person_id="p1", conf=0.9),
        # below surface threshold → skipped
        entity("e3", "commitment", fields={"what": "maybe do it"}, source_ids=["m3"],
               person_id="p1", conf=0.6),
    ]
    return make_comms_db(tmp_path / "comms.db", messages, entities)


def test_dry_run_selects_only_operator_commitments(tmp_path):
    comms = _db(tmp_path)
    res = P.propose_commitments(comms, dry_run=True)
    assert res["dry_run"] is True and res["created"] == 0
    texts = " ".join(i["text"] for i in res["items"])
    assert "send it" in texts        # outbound, >=0.80
    assert "pay back" not in texts   # inbound → excluded
    assert "maybe do it" not in texts  # below surface → excluded
    assert res["candidates"] == 1


def test_commit_creates_and_stamps(tmp_path):
    comms = _db(tmp_path)
    created_texts = []

    def fake_add(text):
        created_texts.append(text)
        return f"i{len(created_texts)}"

    res = P.propose_commitments(comms, dry_run=False, add_inbox=fake_add)
    assert res["created"] == 1
    assert created_texts and "send it" in created_texts[0]
    # entity stamped → ontology_id set
    conn = sqlite3.connect(comms)
    row = conn.execute("SELECT ontology_type, ontology_id FROM message_entities WHERE id='e1'").fetchone()
    conn.close()
    assert row[0] == "work_inbox" and row[1] == "i1"


def test_no_reproposal_after_stamp(tmp_path):
    """A stamped (already-proposed or dismissed) entity is never re-proposed."""
    comms = _db(tmp_path)
    P.propose_commitments(comms, dry_run=False, add_inbox=lambda t: "i1")
    # second run: e1 now has ontology_id → not selected again
    res2 = P.propose_commitments(comms, dry_run=True)
    assert res2["candidates"] == 0


def test_per_run_cap(tmp_path):
    # ago(5 - i) keeps the original ordering (m0 oldest → m4 newest) while
    # holding all five inside STALE_UNDATED_DAYS, so max_per_run is what caps
    # the result rather than fixtures quietly expiring.
    messages = [msg(f"m{i}", f"promise {i}", ts=ago(5 - i),
                    direction="outbound", person_id="p1") for i in range(5)]
    entities = [entity(f"e{i}", "commitment", fields={"what": f"do {i}"},
                       source_ids=[f"m{i}"], person_id="p1", conf=0.9) for i in range(5)]
    comms = make_comms_db(tmp_path / "comms.db", messages, entities)
    res = P.propose_commitments(comms, dry_run=True, max_per_run=2)
    assert res["candidates"] == 2
