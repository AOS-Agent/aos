"""Engine: watermark resume, threshold flags, checkpoint, spam-terminal, e2e.

The model call is stubbed via the `_SPAWN` seam — no real `claude` is launched,
no quota burned. The fake returns a canned envelope so we can assert storage,
thresholds, and idempotent resume deterministically.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from core.engine.comms.enrich import extract as extract_mod
from core.engine.comms.enrich.config import EnrichConfig
from core.engine.comms.enrich.engine import EnrichEngine

from ._helpers import make_comms_db, msg


class _FakeProc:
    def __init__(self, entities):
        self.pid = os.getpid()  # real pid; success path never signals it
        self.returncode = 0
        self._env = json.dumps({
            "result": json.dumps({"entities": entities}),
            "usage": {"output_tokens": 12},
            "total_cost_usd": 0.05,
            "model": "claude-haiku-4-5-20251001",
        })

    def communicate(self, input=None, timeout=None):
        return self._env, ""

    def wait(self, timeout=None):
        return 0


def _install_fake(monkeypatch, entities_for):
    """entities_for(prompt) -> list[entity dicts]."""
    def fake_spawn(cmd):
        # prompt arrives via communicate(); we don't see it here, so callers key
        # off a fixed entity set. For content-specific asserts, use a closure.
        return _FakeProc(entities_for())
    monkeypatch.setattr(extract_mod, "_SPAWN", fake_spawn)


def _cfg(**kw):
    base = dict(min_batch_msgs=1, max_batch_msgs=40, max_comms_db_bytes=10**12,
                min_disk_free_bytes=0, store_min=0.60, surface_min=0.80)
    base.update(kw)
    return EnrichConfig(**base)


def _seed(tmp_path, n=3):
    msgs = [msg(f"m{i}", f"talking about order {i}", ts=f"2026-03-09T10:0{i}:00",
                person_id="p1") for i in range(n)]
    return make_comms_db(tmp_path / "comms.db", msgs)


def test_stores_above_floor_drops_below(tmp_path, monkeypatch):
    db = _seed(tmp_path, 3)
    entities = [
        {"type": "topic", "fields": {"value": "order"}, "confidence": 0.90, "source_ids": ["m0"]},
        {"type": "commitment", "fields": {"who": "p1", "what": "vague"}, "confidence": 0.50, "source_ids": ["m0"]},
    ]
    _install_fake(monkeypatch, lambda: entities)
    eng = EnrichEngine(_cfg(), db_path=db)
    eng.run(mode="nightly")
    conn = sqlite3.connect(db)
    stored = conn.execute("SELECT entity_type, confidence FROM message_entities").fetchall()
    # 0.50 dropped (< store_min); 0.90 kept, once per batch it appears in.
    assert all(c >= 0.60 for _, c in stored)
    assert ("topic", 0.9) in [(t, round(c, 2)) for t, c in stored]
    conn.close()


def test_surfaceable_is_derived(tmp_path, monkeypatch):
    db = _seed(tmp_path, 1)
    entities = [
        {"type": "transaction", "fields": {"merchant": "Costco"}, "confidence": 0.90, "source_ids": ["m0"]},
        {"type": "topic", "fields": {"value": "chat"}, "confidence": 0.65, "source_ids": ["m0"]},
    ]
    _install_fake(monkeypatch, lambda: entities)
    eng = EnrichEngine(_cfg(), db_path=db)
    eng.run(mode="nightly")
    conn = sqlite3.connect(db)
    surfaceable = conn.execute(
        "SELECT COUNT(*) FROM message_entities WHERE confidence >= 0.80 AND status='active'").fetchone()[0]
    band = conn.execute(
        "SELECT COUNT(*) FROM message_entities WHERE confidence >= 0.60 AND confidence < 0.80").fetchone()[0]
    assert surfaceable == 1  # the 0.90 transaction
    assert band == 1         # the 0.65 topic — stored, retrieval-only
    conn.close()


def test_watermark_resume_no_rework(tmp_path, monkeypatch):
    db = _seed(tmp_path, 3)
    calls = {"n": 0}
    def entities():
        calls["n"] += 1
        return [{"type": "topic", "fields": {"value": "x"}, "confidence": 0.9, "source_ids": ["m0"]}]
    _install_fake(monkeypatch, entities)
    eng = EnrichEngine(_cfg(), db_path=db)
    s1 = eng.run(mode="nightly")
    first_calls = calls["n"]
    assert first_calls >= 1 and s1["messages_extracted"] == 3
    s2 = eng.run(mode="nightly")  # everything watermarked already
    assert calls["n"] == first_calls  # no new model calls
    assert s2["candidates"] == 0


def test_spam_is_terminal(tmp_path, monkeypatch):
    spam = msg("s1", "Your account has been suspended, click here to verify your account",
               ts="2026-04-01T00:00:00", channel="email",
               channel_metadata=json.dumps({"labels": ["INBOX"]}))
    good = msg("g1", "lunch tomorrow?", ts="2026-04-01T10:00:00", person_id="p1")
    db = make_comms_db(tmp_path / "comms.db", [spam, good])
    _install_fake(monkeypatch, lambda: [
        {"type": "question_open", "fields": {"value": "lunch tomorrow?"}, "confidence": 0.9, "source_ids": ["g1"]}])
    eng = EnrichEngine(_cfg(), db_path=db)
    stats = eng.run(mode="nightly")
    assert stats["skipped_spam"] == 1
    conn = sqlite3.connect(db)
    wm = dict(conn.execute("SELECT message_id, status FROM message_extraction").fetchall())
    assert wm["s1"] == "skipped_spam"
    assert wm["g1"] == "extracted"
    # Re-run: spam never re-attempted, nothing new.
    s2 = eng.run(mode="nightly")
    assert s2["candidates"] == 0
    conn.close()


def test_new_version_supersedes(tmp_path, monkeypatch):
    db = _seed(tmp_path, 1)
    _install_fake(monkeypatch, lambda: [
        {"type": "topic", "fields": {"value": "order"}, "confidence": 0.9, "source_ids": ["m0"]}])
    EnrichEngine(_cfg(extractor_version="extract@1"), db_path=db).run(mode="nightly")
    EnrichEngine(_cfg(extractor_version="extract@2"), db_path=db).run(mode="nightly")
    conn = sqlite3.connect(db)
    statuses = dict(conn.execute(
        "SELECT extractor_version, status FROM message_entities").fetchall())
    assert statuses.get("extract@1") == "superseded"
    assert statuses.get("extract@2") == "active"
    conn.close()


def test_dry_run_extracts_nothing(tmp_path, monkeypatch):
    db = _seed(tmp_path, 3)
    _install_fake(monkeypatch, lambda: [])
    stats = EnrichEngine(_cfg(), db_path=db).run(mode="nightly", dry_run=True)
    assert stats["dry_run"] and stats["candidates"] == 3
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM message_entities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM message_extraction").fetchone()[0] == 0
    conn.close()


def test_storage_gate_blocks_run(tmp_path, monkeypatch):
    db = _seed(tmp_path, 1)
    _install_fake(monkeypatch, lambda: [])
    eng = EnrichEngine(_cfg(max_comms_db_bytes=1), db_path=db)  # impossible ceiling
    from core.engine.comms.enrich.gates import StorageGateError
    with pytest.raises(StorageGateError):
        eng.run(mode="nightly")
