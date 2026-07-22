"""Sensors — comms_entities + initiative_drift. No LLM calls; isolated
tmp databases and dirs, real signals-store writes via monkeypatched path."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.engine.loop import signals as sig  # noqa: E402
from core.engine.loop.sensors import comms_entities, initiative_drift  # noqa: E402

_SIGNALS_SCHEMA = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY, sensor TEXT NOT NULL, signal_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, source_refs TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0, project_key TEXT, created_at TEXT NOT NULL
);
"""

_ENTITIES_SCHEMA = """
CREATE TABLE message_entities (
    id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, value TEXT,
    fields_json TEXT NOT NULL, confidence REAL NOT NULL,
    source_ids TEXT NOT NULL, person_id TEXT, channel TEXT,
    batch_key TEXT NOT NULL, extractor_version TEXT NOT NULL,
    model TEXT NOT NULL, created_at TEXT NOT NULL,
    ontology_type TEXT, ontology_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);
"""


@pytest.fixture()
def signals_db(tmp_path, monkeypatch):
    path = tmp_path / "qareen.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SIGNALS_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(sig, "_db_path", lambda: path)
    return path


def _all_signals(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM signals").fetchall()
    conn.close()
    return rows


def _make_comms_db(tmp_path, rows):
    path = tmp_path / "comms.db"
    conn = sqlite3.connect(path)
    conn.executescript(_ENTITIES_SCHEMA)
    for i, (etype, person, age_hours, status) in enumerate(rows):
        conn.execute(
            "INSERT INTO message_entities VALUES (?,?,?,?,?,?,?,?,?,?,?,"
            "datetime('now', ?),?,?,?)",
            (f"ent_{i:04d}", etype, f"value {i}", "{}", 0.9, '["m1"]',
             person, "imessage", "bk", "extract@1", "haiku",
             f"-{age_hours} hours", None, None, status),
        )
    conn.commit()
    conn.close()
    return path


# ── comms_entities ──────────────────────────────────────────────────────────

def test_commitments_signal_with_taint_and_provenance(signals_db, tmp_path):
    comms = _make_comms_db(tmp_path, [("commitment", "p_1", 2, "active"),
                                      ("commitment", "p_2", 3, "active")])
    ids = comms_entities.run(db_path=comms)
    assert len(ids) == 1
    row = _all_signals(signals_db)[0]
    assert row["sensor"] == "comms_entities"
    assert row["signal_type"] == "daily_commitment"
    assert row["tainted"] == 1  # external content is ALWAYS tainted
    payload = json.loads(row["payload_json"])
    assert payload["count"] == 2 and payload["distinct_people"] == 2
    assert json.loads(row["source_refs"]) == ["ent_0000", "ent_0001"]


def test_floors_suppress_noise(signals_db, tmp_path):
    # 3 open questions (< floor of 10) and 2 topics (never signaled)
    comms = _make_comms_db(tmp_path, [("question_open", "p_1", 1, "active")] * 3
                                     + [("topic", "p_1", 1, "active")] * 2)
    assert comms_entities.run(db_path=comms) == []
    assert _all_signals(signals_db) == []


def test_window_and_status_filters(signals_db, tmp_path):
    comms = _make_comms_db(tmp_path, [
        ("commitment", "p_1", 30, "active"),      # outside 24h window
        ("commitment", "p_1", 2, "dismissed"),    # not active
    ])
    assert comms_entities.run(db_path=comms) == []


def test_missing_comms_db_is_graceful(signals_db, tmp_path):
    assert comms_entities.run(db_path=tmp_path / "nope.db") == []


# ── initiative_drift ────────────────────────────────────────────────────────

def _write_initiative(root, name, status, updated=None):
    fm = f"---\ntitle: {name}\nstatus: {status}\n"
    if updated:
        fm += f"updated: {updated}\n"
    (root / f"{name}.md").write_text(fm + "---\n# x\n")


def test_stale_executing_flagged(signals_db, tmp_path):
    root = tmp_path / "initiatives"
    root.mkdir()
    _write_initiative(root, "old-exec", "executing", "2026-01-01")
    _write_initiative(root, "fresh-exec", "executing",
                      __import__("datetime").date.today().isoformat())
    _write_initiative(root, "done-thing", "done", "2026-01-01")
    ids = initiative_drift.run(initiatives_dir=root)
    stale = [r for r in _all_signals(signals_db)
             if r["signal_type"] == "stale_executing"]
    assert len(stale) == 1
    assert json.loads(stale[0]["payload_json"])["file"] == "old-exec.md"
    assert stale[0]["tainted"] == 0  # first-party system state
    assert ids


def test_sprawl_signal_over_floor(signals_db, tmp_path):
    root = tmp_path / "initiatives"
    root.mkdir()
    today = __import__("datetime").date.today().isoformat()
    for i in range(9):  # > SPRAWL_FLOOR of 8 active
        _write_initiative(root, f"init-{i}", "executing", today)
    initiative_drift.run(initiatives_dir=root)
    sprawl = [r for r in _all_signals(signals_db)
              if r["signal_type"] == "portfolio_sprawl"]
    assert len(sprawl) == 1
    assert json.loads(sprawl[0]["payload_json"])["active_count"] == 9


def test_empty_dir_is_graceful(signals_db, tmp_path):
    assert initiative_drift.run(initiatives_dir=tmp_path / "nope") == []
