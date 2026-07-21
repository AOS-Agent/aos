"""Mention hook: cheap capitalized-token match, file-only, <100ms, privacy-safe.

The hook must NOT do DB or model work — all resolution is a names.json lookup
plus at most two snapshot file reads. We build a cache dir directly (privacy is
already enforced when snapshots are written, so a restricted person simply has
no snapshot to read here) and assert correctness + latency.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location(
    "mention_context", _REPO / "core" / "hooks" / "mention_context.py")
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _cache(tmp_path, index, snaps):
    (tmp_path / "persons").mkdir(parents=True, exist_ok=True)
    (tmp_path / "names.json").write_text(json.dumps(index))
    for pid, snap in snaps.items():
        (tmp_path / "persons" / f"{pid}.json").write_text(json.dumps(snap))
    return tmp_path


def test_matches_full_name_and_renders(tmp_path):
    cache = _cache(tmp_path, {"faisal khan": "p1", "faisal": "p1"}, {
        "p1": {"person_id": "p1", "name": "Faisal Khan",
               "last_interaction": "2026-07-10T00:00:00",
               "owed_by_you": [{"what": "send the lease", "due": "Friday"}],
               "owed_to_you": [], "unanswered_questions": [{"q": "when signing?"}],
               "recent_topics": ["lease", "deposit"]}})
    out = hook.resolve_and_render("Did I settle up with Faisal Khan?", cache)
    assert "Faisal Khan" in out
    assert "send the lease" in out
    assert "when signing?" in out


def test_no_match_returns_blank(tmp_path):
    cache = _cache(tmp_path, {"faisal": "p1"}, {"p1": {"name": "Faisal"}})
    assert hook.resolve_and_render("what should I do today?", cache) == ""


def test_stopwords_never_match(tmp_path):
    # A person literally aliased nothing; common openers must not hit.
    cache = _cache(tmp_path, {"the": "p1"}, {"p1": {"name": "X"}})
    # 'the' is a stopword → filtered before lookup even though it's in the index
    assert hook.resolve_and_render("The meeting is today", cache) == ""


def test_restricted_person_has_no_snapshot(tmp_path):
    # names.json maps the name but the snapshot file is absent (privacy filtered
    # at build time) → nothing injected.
    cache = _cache(tmp_path, {"banker": "p2"}, {})
    assert hook.resolve_and_render("call the Banker", cache) == ""


def test_caps_at_two_people(tmp_path):
    cache = _cache(tmp_path, {"ali": "p1", "omar": "p2", "sara": "p3"},
                   {"p1": {"name": "Ali"}, "p2": {"name": "Omar"}, "p3": {"name": "Sara"}})
    out = hook.resolve_and_render("Ali and Omar and Sara", cache)
    assert out.count("**") <= hook._MAX_PEOPLE * 2  # bold markers per person


def test_latency_under_budget(tmp_path):
    # Large index, realistic prompt. Pure file-read path must be well <100ms.
    index = {f"person{i}": f"p{i}" for i in range(5000)}
    index["faisal khan"] = "pX"
    snaps = {"pX": {"name": "Faisal Khan", "last_interaction": "2026-07-10",
                    "owed_by_you": [{"what": "x"}], "owed_to_you": [],
                    "unanswered_questions": [], "recent_topics": ["a"]}}
    cache = _cache(tmp_path, index, snaps)
    t = time.perf_counter()
    out = hook.resolve_and_render("Following up with Faisal Khan on the deal", cache)
    elapsed_ms = (time.perf_counter() - t) * 1000
    assert "Faisal Khan" in out
    assert elapsed_ms < 100, f"hook took {elapsed_ms:.1f}ms (budget 100ms)"


def test_candidate_keys_prioritizes_full_name():
    keys = hook._candidate_keys("Talk to Abu Bakr about it")
    assert keys[0] == "abu bakr"  # multi-word run first
    assert "abu" in keys and "bakr" in keys
