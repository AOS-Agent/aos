"""Batch judge — prompt building, strict parsing, retry/bisect/degrade
ladder, prefilter, pipeline assembly, version hash. No real LLM calls."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.engine.loop import judge as J  # noqa: E402


def _items(n):
    return [{"id": i + 1, "text": f"message {i + 1}", "prev_snippet": None} for i in range(n)]


def _ok_response(ids, label="none"):
    return json.dumps({"results": [
        {"id": i, "machine_text": False, "label": label} for i in ids
    ]})


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_valid():
    out = J._parse_batch(_ok_response([1, 2, 3]), [1, 2, 3])
    assert [r["id"] for r in out] == [1, 2, 3]
    assert all(r["judge_error"] is False for r in out)


def test_parse_strips_fences_and_prose():
    raw = "Here you go:\n```json\n" + _ok_response([1]) + "\n```"
    assert J._parse_batch(raw, [1]) is not None


def test_parse_rejects_wrong_count_and_ids():
    assert J._parse_batch(_ok_response([1, 2]), [1, 2, 3]) is None
    assert J._parse_batch(_ok_response([1, 2, 4]), [1, 2, 3]) is None
    dup = json.dumps({"results": [{"id": 1, "machine_text": False, "label": "none"}] * 2})
    assert J._parse_batch(dup, [1, 2]) is None


def test_parse_rejects_bad_label():
    raw = json.dumps({"results": [{"id": 1, "machine_text": False, "label": "angry"}]})
    assert J._parse_batch(raw, [1]) is None


def test_machine_text_forces_none_structurally():
    raw = json.dumps({"results": [{"id": 1, "machine_text": True, "label": "frustration"}]})
    (r,) = J._parse_batch(raw, [1])
    assert r["label"] == "none" and r["machine_text"] is True


# ── retry / bisect / degrade ────────────────────────────────────────────────

def test_retry_once_then_success(monkeypatch):
    calls = []

    async def fake_complete(prompt, model=None, system=None, timeout_s=None):
        calls.append(prompt)
        return "garbage" if len(calls) == 1 else _ok_response([1, 2])

    monkeypatch.setattr(J.llm, "complete", fake_complete)
    out = asyncio.run(J.judge_batch(_items(2)))
    assert len(calls) == 2
    assert "malformed" in calls[1]  # corrective suffix in USER turn
    assert [r["id"] for r in out] == [1, 2]


def test_bisect_isolates_poison_item(monkeypatch):
    async def fake_complete(prompt, model=None, system=None, timeout_s=None):
        # any batch containing "message 3" fails to parse; others succeed
        if "message 3" in prompt:
            return "unparseable nonsense"
        ids = [int(l.split('"')[1]) for l in prompt.splitlines() if l.startswith("<item")]
        return _ok_response(ids)

    monkeypatch.setattr(J.llm, "complete", fake_complete)
    out = asyncio.run(J.judge_batch(_items(4)))
    assert [r["id"] for r in out] == [1, 2, 3, 4]
    assert out[2]["judge_error"] is True and out[2]["label"] == "none"
    assert all(r["judge_error"] is False for i, r in enumerate(out) if i != 2)


def test_chunking_over_batch_size(monkeypatch):
    batch_sizes = []

    async def fake_complete(prompt, model=None, system=None, timeout_s=None):
        ids = [int(l.split('"')[1]) for l in prompt.splitlines() if l.startswith("<item")]
        batch_sizes.append(len(ids))
        return _ok_response(ids)

    monkeypatch.setattr(J.llm, "complete", fake_complete)
    out = asyncio.run(J.judge_batch(_items(65)))
    assert len(out) == 65
    assert batch_sizes == [30, 30, 5]


# ── prefilter ───────────────────────────────────────────────────────────────

def test_prefilter_machine_and_trivial():
    assert J.prefilter("You are Envoy, an AI agent conducting...") == "none"
    assert J.prefilter("Batch: 17 message(s), channel=whatsapp...") == "none"
    assert J.prefilter("<teammate-message teammate_id='x'>hi</teammate-message>") == "none"
    assert J.prefilter("ok") == "none"
    assert J.prefilter("ship it") == "none"
    assert J.prefilter("/Users/x/some/file.md") == "none"
    assert J.prefilter("") == "none"


def test_prefilter_passes_real_messages():
    assert J.prefilter("no thats not what i meant... dont strip it") is None
    assert J.prefilter("the glyphs are cut off in 1:1") is None
    assert J.prefilter("okay so whats next on the table") is None  # >24 chars


# ── pipeline ────────────────────────────────────────────────────────────────

def test_pipeline_prefilters_before_judge(monkeypatch):
    judged_texts = []

    async def fake_complete(prompt, model=None, system=None, timeout_s=None):
        ids = [int(l.split('"')[1]) for l in prompt.splitlines() if l.startswith("<item")]
        judged_texts.append(len(ids))
        return _ok_response(ids, label="correction")

    monkeypatch.setattr(J.llm, "complete", fake_complete)
    items = [
        {"id": 0, "text": "You are Envoy, an AI agent doing things", "prev_snippet": None},
        {"id": 1, "text": "you did this completely wrong my friend", "prev_snippet": None},
        {"id": 2, "text": "ok", "prev_snippet": None},
    ]
    out = asyncio.run(J.classify_pipeline(items))
    assert [r["id"] for r in out] == [0, 1, 2]  # input order preserved
    assert out[0]["prefiltered"] and out[0]["label"] == "none"
    assert out[2]["prefiltered"] and out[2]["label"] == "none"
    assert out[1]["label"] == "correction" and "prefiltered" not in out[1]
    assert judged_texts == [1]  # only one item reached the judge


# ── version hash ────────────────────────────────────────────────────────────

def test_version_hash_covers_batch_format(monkeypatch):
    h1 = J.version_hash()
    monkeypatch.setattr(J, "BATCH_FORMAT", "batch-v2;size=50;item=xml;out=json-results")
    assert J.version_hash() != h1


def test_version_hash_covers_prefilter(monkeypatch):
    h1 = J.version_hash()
    monkeypatch.setattr(J, "PREFILTER_VERSION", "pf-v2")
    assert J.version_hash() != h1
