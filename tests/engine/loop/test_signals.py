"""Intelligence Loop Phase 1 — signals + proposals writer library contract.

Covers: append/read roundtrip, provenance enforcement, taint inheritance,
evidence validation, lazy expiry idempotency, guarded status transitions,
and the engagement helper's counts-only rule.

Isolated: every test points the module at a throwaway SQLite DB under
pytest's tmp_path via monkeypatch on the module-level `_db_path()` function —
never the real ~/.aos/data/qareen.db.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Repo-root import (matches tests/engine/comms pattern). NOT `from engine.loop
# import ...`: conftest puts core/engine/work on sys.path, whose engine.py
# shadows the `engine` namespace package (regular modules always win).
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.engine.loop import signals as sig  # noqa: E402

_SCHEMA_SQL = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY, sensor TEXT NOT NULL, signal_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, source_refs TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0, project_key TEXT, created_at TEXT NOT NULL
);
CREATE TABLE proposals (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, diff_type TEXT NOT NULL,
    body TEXT NOT NULL, evidence_refs TEXT NOT NULL, tainted INTEGER NOT NULL,
    project_key TEXT, status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    decided_at TEXT, outcome_note TEXT
);
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "qareen.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(sig, "_db_path", lambda: path)
    return path


def _raw(db_path, table, id_):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
    conn.close()
    return row


# ── signals: append + read roundtrip ────────────────────────────────────────

def test_append_signal_roundtrip(db):
    sid = sig.append_signal(
        sensor="friction_judge", signal_type="repeated_correction",
        payload={"topic": "commit messages"}, source_refs=["session:abc123"],
    )
    row = _raw(db, "signals", sid)
    assert row is not None
    assert row["sensor"] == "friction_judge"
    assert json.loads(row["payload_json"]) == {"topic": "commit messages"}
    assert json.loads(row["source_refs"]) == ["session:abc123"]
    assert row["tainted"] == 0


def test_append_signal_rejects_empty_source_refs(db):
    with pytest.raises(ValueError):
        sig.append_signal(
            sensor="friction_judge", signal_type="x", payload={}, source_refs=[],
        )


# ── proposals: evidence + taint inheritance ─────────────────────────────────

def test_proposal_inherits_taint_from_evidence(db):
    clean = sig.append_signal("initiative_drift", "stale", {}, ["log:2026-07-20"], tainted=False)
    tainted = sig.append_signal("comms_entities", "mention", {}, ["im-1"], tainted=True)

    pid = sig.create_proposal(
        title="Update CLAUDE.md", diff_type="claude_md", body="...",
        evidence_refs=[clean, tainted],
    )
    row = _raw(db, "proposals", pid)
    assert row["tainted"] == 1


def test_proposal_not_tainted_when_all_evidence_clean(db):
    clean = sig.append_signal("initiative_drift", "stale", {}, ["log:2026-07-20"])
    pid = sig.create_proposal(
        title="Update CLAUDE.md", diff_type="claude_md", body="...", evidence_refs=[clean],
    )
    row = _raw(db, "proposals", pid)
    assert row["tainted"] == 0


def test_proposal_rejects_empty_evidence_refs(db):
    with pytest.raises(ValueError):
        sig.create_proposal(title="x", diff_type="other", body="x", evidence_refs=[])


def test_proposal_rejects_unknown_evidence_id(db):
    real = sig.append_signal("initiative_drift", "stale", {}, ["log:2026-07-20"])
    with pytest.raises(ValueError):
        sig.create_proposal(
            title="x", diff_type="other", body="x", evidence_refs=[real, "sig_doesnotexist"],
        )


# ── lazy expiry ──────────────────────────────────────────────────────────────

def test_lazy_expire_flips_only_expired_proposed_or_surfaced(db):
    ev = sig.append_signal("engagement", "counts", {"n": 1}, ["engagement:self"])
    expired = sig.create_proposal("expired one", "config", "body", [ev], ttl_days=-1)
    fresh = sig.create_proposal("fresh one", "config", "body", [ev], ttl_days=14)

    flipped = sig.lazy_expire()
    assert flipped == 1
    assert _raw(db, "proposals", expired)["status"] == "lapsed"
    assert _raw(db, "proposals", fresh)["status"] == "proposed"


def test_lazy_expire_is_idempotent(db):
    ev = sig.append_signal("engagement", "counts", {"n": 1}, ["engagement:self"])
    sig.create_proposal("expired one", "config", "body", [ev], ttl_days=-1)

    first = sig.lazy_expire()
    second = sig.lazy_expire()
    assert first == 1
    assert second == 0


def test_approved_proposal_never_lapses(db):
    ev = sig.append_signal("engagement", "counts", {"n": 1}, ["engagement:self"])
    pid = sig.create_proposal("approve me", "config", "body", [ev], ttl_days=-1)
    sig.mark_status(pid, "approved")

    flipped = sig.lazy_expire()
    assert flipped == 0
    assert _raw(db, "proposals", pid)["status"] == "approved"


# ── status transitions ───────────────────────────────────────────────────────

def test_illegal_transition_raises(db):
    ev = sig.append_signal("engagement", "counts", {"n": 1}, ["engagement:self"])
    pid = sig.create_proposal("x", "config", "body", [ev])
    with pytest.raises(ValueError):
        sig.mark_status(pid, "applied")  # proposed -> applied is not legal


def test_legal_transition_chain_sets_decided_at(db):
    ev = sig.append_signal("engagement", "counts", {"n": 1}, ["engagement:self"])
    pid = sig.create_proposal("x", "config", "body", [ev])

    sig.mark_status(pid, "surfaced")
    assert _raw(db, "proposals", pid)["decided_at"] is None

    sig.mark_status(pid, "approved", note="looks good")
    row = _raw(db, "proposals", pid)
    assert row["status"] == "approved"
    assert row["decided_at"] is not None
    assert row["outcome_note"] == "looks good"

    sig.mark_status(pid, "applied")
    assert _raw(db, "proposals", pid)["status"] == "applied"


# ── engagement helper ────────────────────────────────────────────────────────

def test_record_engagement_accepts_int_counts(db):
    sid = sig.record_engagement({"messages_sent": 3, "tasks_completed": 1})
    row = _raw(db, "signals", sid)
    assert row["sensor"] == "engagement"
    assert json.loads(row["payload_json"]) == {"messages_sent": 3, "tasks_completed": 1}
    assert json.loads(row["source_refs"]) == ["engagement:self"]


def test_record_engagement_rejects_non_int_values(db):
    with pytest.raises(ValueError):
        sig.record_engagement({"messages_sent": "three"})
    with pytest.raises(ValueError):
        sig.record_engagement({"ratio": 0.5})
    with pytest.raises(ValueError):
        sig.record_engagement({"flag": True})  # bool is an int subclass — rejected anyway
