"""Nudge lifecycle: dismiss/act record feedback, expire GCs stale pending."""
from __future__ import annotations

import sqlite3
import time

from core.engine.comms.ambient import nudges as N

from ._helpers import make_people_db


def _people(tmp_path, nudges):
    return make_people_db(tmp_path / "people.db",
                          people=[{"id": "p1", "canonical_name": "Bilal"}],
                          nudges=nudges)


def test_dismiss_sets_status_and_feedback(tmp_path):
    pdb = _people(tmp_path, [{"id": "iq1", "person_id": "p1", "surface_type": "drift",
                              "content": "fading with Bilal"}])
    assert N.dismiss("iq1", pdb) is True
    conn = sqlite3.connect(pdb)
    status = conn.execute("SELECT status FROM intelligence_queue WHERE id='iq1'").fetchone()[0]
    fb = conn.execute("SELECT operator_action, surface_type FROM surface_feedback").fetchone()
    conn.close()
    assert status == "dismissed"
    assert fb == ("dismissed", "drift")


def test_act_sets_status_and_feedback(tmp_path):
    pdb = _people(tmp_path, [{"id": "iq1", "person_id": "p1", "surface_type": "reconnect",
                              "content": "reach out"}])
    assert N.act("iq1", pdb) is True
    conn = sqlite3.connect(pdb)
    assert conn.execute("SELECT status FROM intelligence_queue WHERE id='iq1'").fetchone()[0] == "acted"
    assert conn.execute("SELECT operator_action FROM surface_feedback").fetchone()[0] == "acted"
    conn.close()


def test_dismiss_unknown_or_nonpending_returns_false(tmp_path):
    pdb = _people(tmp_path, [{"id": "iq1", "person_id": "p1", "surface_type": "drift",
                              "status": "dismissed"}])
    assert N.dismiss("iq1", pdb) is False   # already non-pending
    assert N.dismiss("nope", pdb) is False   # unknown id


def test_expire_stale(tmp_path):
    now = int(time.time())
    old = now - 40 * 86400
    fresh = now - 5 * 86400
    pdb = _people(tmp_path, [
        {"id": "old1", "person_id": "p1", "surface_type": "drift", "created_at": old},
        {"id": "new1", "person_id": "p1", "surface_type": "drift", "created_at": fresh},
    ])
    n = N.expire_stale(pdb, after_days=30, now_ts=now)
    assert n == 1
    conn = sqlite3.connect(pdb)
    rows = dict(conn.execute("SELECT id, status FROM intelligence_queue").fetchall())
    conn.close()
    assert rows["old1"] == "expired" and rows["new1"] == "pending"


def test_missing_people_db_is_safe(tmp_path):
    assert N.dismiss("x", tmp_path / "nope.db") is False
    assert N.expire_stale(tmp_path / "nope.db") == 0
    assert N.list_nudges(tmp_path / "nope.db") == []
