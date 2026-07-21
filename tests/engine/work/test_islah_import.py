"""Kanban Phase 3 — islah bugs.yaml import (the data plane).

Covers, against a FAKE ledger fixture (never the operator's real bugs.yaml):
  * round-trip acceptance (qg#1-shaped ``ex#1``): import → read via the work
    backend → every field + the full attempt/proof narrative survive intact,
    with ORIGINAL timestamps in the activity ts.
  * idempotency: a second import creates nothing and duplicates no activity.
  * timestamp preservation: reconstructed beats carry the ledger's timestamps,
    not import-time.
  * stage/status mapping: islah's 13 states land on the right bug pipeline
    stage and coarse board status.
  * the mirror path: a bug appended to the ledger after the first import is
    picked up on the next run; existing bugs are untouched.

Isolated via the work_env fixture (throwaway AOS_WORK_DB).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

_FIXTURE = _ROOT / "tests" / "fixtures" / "islah_bugs_fake.yaml"


def _import(engine, path):
    from core.engine.work.intake.islah_import import import_bugs
    return import_bugs(path, engine=engine)


def _activity(engine, task_id):
    return engine.get_task_activity(task_id)


def _kinds(entries):
    return [e["kind"] for e in entries]


# ── round-trip acceptance (the qg#1 fixture) ─────────────────────────────────

def test_roundtrip_every_field_and_narrative_intact(work_env):
    eng = work_env["engine"]
    res = _import(eng, _FIXTURE)

    assert res["total"] == 3
    assert res["created"] == 3
    assert res["skipped"] == 0

    ex1 = next(t for t in res["tasks"] if t["islah_id"] == "ex#1")
    tid = ex1["task_id"]

    task = eng.get_task(tid)
    # Stage + coarse status: awaiting-approval → in_review (started).
    assert task["stage"] == "awaiting-approval"
    assert task["status"] == "in_review"
    assert task["pipeline"] == "bug"

    # Every carried field survives in fields JSON, unflattened.
    f = task["fields"]
    assert f["islah_id"] == "ex#1"
    assert f["severity"] == 2
    assert f["build"] == "55"
    assert f["screen"] == "Study mode"
    assert f["classification"] == "visual"
    assert "null gloss" in f["root_cause"]
    assert f["code_refs"] == ["WordStore.swift:157", "ContentStore.swift:100"]
    assert f["branch"] == "fix/ex1-glyph-null-gloss"
    assert f["commits"][0]["sha"] == "abc1234"
    assert f["commits"][0]["files"] == [
        "Sources/Core/WordStore.swift", "Sources/Features/WordDetailPanel.swift"
    ]

    # source_ref round-trips through the description meta.
    assert task["source_ref"] == "islah:ex#1"

    story = _activity(eng, tid)
    kinds = _kinds(story)
    assert kinds[0] == "created"
    # triaged (1) + two fix attempts = three "attempt" beats.
    assert kinds.count("attempt") == 3
    assert "proof" in kinds
    assert "linked" in kinds
    assert story[-1]["kind"] == "status_changed"
    assert story[-1]["data"]["to"] == "in_review"

    # The attempt narrative survives verbatim — nothing flattened.
    fix_beats = [e for e in story if e["kind"] == "attempt" and e["body"].startswith("Attempt")]
    assert [b["data"]["n"] for b in fix_beats] == [1, 2]
    assert fix_beats[1]["data"]["sha"] == "abc1234"
    assert fix_beats[1]["data"]["gate_result"] == "build_pass"
    proof = next(e for e in story if e["kind"] == "proof")
    assert proof["data"]["kind"] == "before"


def test_original_timestamps_preserved(work_env):
    eng = work_env["engine"]
    res = _import(eng, _FIXTURE)
    tid = next(t["task_id"] for t in res["tasks"] if t["islah_id"] == "ex#1")
    story = _activity(eng, tid)

    created = story[0]
    assert created["ts"] == "2026-07-10T20:05:54Z"      # reported, not import-time

    fix_beats = [e for e in story if e["kind"] == "attempt" and e["body"].startswith("Attempt")]
    assert fix_beats[0]["ts"] == "2026-07-12T18:39:19Z"
    assert fix_beats[1]["ts"] == "2026-07-12T18:52:40Z"
    status = story[-1]
    assert status["ts"] == "2026-07-21T18:19:36Z"       # updated


# ── idempotency (import + mirror share one path) ─────────────────────────────

def test_reimport_is_idempotent(work_env):
    eng = work_env["engine"]
    first = _import(eng, _FIXTURE)
    tid = next(t["task_id"] for t in first["tasks"] if t["islah_id"] == "ex#1")
    story_before = _activity(eng, tid)

    second = _import(eng, _FIXTURE)
    assert second["created"] == 0
    assert second["skipped"] == 3
    assert second["activities"] == 0

    # No duplicate tasks, no duplicate activity.
    story_after = _activity(eng, tid)
    assert len(story_after) == len(story_before)
    tasks_for_ex1 = [t for t in eng.get_all_tasks()
                     if (t.get("fields") or {}).get("islah_id") == "ex#1"]
    assert len(tasks_for_ex1) == 1


def test_mirror_picks_up_a_new_ledger_row(work_env, tmp_path):
    eng = work_env["engine"]
    _import(eng, _FIXTURE)

    # Operator's islah CLI appends a new bug to the ledger before cutover.
    raw = yaml.safe_load(_FIXTURE.read_text())
    raw["bugs"].append({
        "id": "ex#4", "title": "New crash on launch", "status": "new",
        "kind": "bug", "app": "example-app", "source": "asc-crash",
        "source_ref": "asc-crash:fake4", "reporter": "tester",
        "reported": "2026-07-22T09:00:00Z", "severity": 1,
        "classification": "crash",
        "created": "2026-07-22T09:00:00Z", "updated": "2026-07-22T09:00:00Z",
    })
    grown = tmp_path / "bugs.yaml"
    grown.write_text(yaml.safe_dump(raw, sort_keys=False))

    res = _import(eng, grown)
    assert res["created"] == 1      # only the new row
    assert res["skipped"] == 3      # the originals untouched
    new = next(t for t in res["tasks"] if t["islah_id"] == "ex#4")
    assert eng.get_task(new["task_id"])["fields"]["classification"] == "crash"


# ── stage / status mapping ───────────────────────────────────────────────────

def test_stage_and_status_mapping(work_env):
    eng = work_env["engine"]
    res = _import(eng, _FIXTURE)
    by_islah = {t["islah_id"]: t for t in res["tasks"]}

    new_task = eng.get_task(by_islah["ex#2"]["task_id"])
    assert new_task["stage"] == "new"
    assert new_task["status"] == "triage"
    # A fresh 'new' bug has only its created beat — no status move to narrate.
    assert _kinds(_activity(eng, new_task["id"])) == ["created"]

    confirmed = eng.get_task(by_islah["ex#3"]["task_id"])
    assert confirmed["stage"] == "confirmed"
    assert confirmed["status"] == "todo"


def test_missing_ledger_is_a_noop(work_env, tmp_path):
    eng = work_env["engine"]
    res = _import(eng, tmp_path / "nope.yaml")
    assert res == {"total": 0, "created": 0, "skipped": 0, "activities": 0,
                   "tasks": [], "note": f"no ledger at {tmp_path / 'nope.yaml'}"}
