"""Kanban Phase 2 — the activity log (the narrative layer).

Covers:
  * append-only enforcement — the adapter exposes no mutate-past-entry path,
    and the SQL triggers reject UPDATE/DELETE of a stored entry.
  * auto-narration — every mutation path through the adapter (create, status,
    priority edit, delegate, hold, start, complete, subtask cascade) writes its
    own narrative line, from the ONE choke point (not per caller).
  * manual-append guard — agents may append attempt/proof/comment/… but never
    forge an auto-narration kind (status_changed/delegated/…).
  * backfill — entity_history rows migrate into the narrative log once,
    idempotently (re-running the migration adds nothing).
  * event emission — a hand-append emits task.activity for SSE liveness.
  * API action — the governed append_activity action writes through the adapter.
  * the islah narrative fixture — a qg#1-shaped bug walks the full story
    (created → triaged → delegated → attempt → proof → in-review) and the
    timeline reads coherently with NOTHING flattened.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


def _kinds(entries):
    return [e["kind"] for e in entries]


def _bodies(entries):
    return [e["body"] for e in entries]


# ── append-only enforcement ──────────────────────────────────────────────────

def test_adapter_exposes_no_mutation_of_past_entries(work_env):
    adapter = work_env["engine"]._get_adapter()
    # The narrative log is append-only by construction: there is an append and
    # a list, and deliberately NO update/delete counterpart.
    assert hasattr(adapter, "append_activity")
    assert hasattr(adapter, "list_activity")
    assert not hasattr(adapter, "update_activity")
    assert not hasattr(adapter, "delete_activity")
    assert not hasattr(adapter, "edit_activity")


def test_triggers_reject_update_and_delete(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    adapter = eng._get_adapter()
    adapter.append_activity(tid, "comment", "original", actor="operator", manual=True)
    conn = adapter._conn
    with pytest.raises(sqlite3.Error):
        conn.execute("UPDATE task_activity SET body = 'tampered' WHERE task_id = ?", (tid,))
    with pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM task_activity WHERE task_id = ?", (tid,))
    # The original survives untouched.
    rows = adapter.list_activity(tid)
    assert any(r["body"] == "original" for r in rows)


# ── auto-narration on every mutation path ────────────────────────────────────

def test_create_narrates(work_env):
    eng = work_env["engine"]
    eng.add_project("AOS", short_id="aos", project_id="aos")
    t = eng.add_task("A task", project="aos")
    entries = eng.get_task_activity(t["id"])
    assert _kinds(entries) == ["created"]
    assert 'Created "A task"' in entries[0]["body"]


def test_status_edit_delegate_hold_start_complete_all_narrate(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]

    eng.start_task(tid)                       # status_changed (Started)
    eng.update_task(tid, priority=1)          # edited (priority)
    eng.delegate_task(tid, "advisor")         # delegated
    eng.hold_task(tid)                        # held
    eng.update_task(tid, status="waiting")    # status_changed
    eng.complete_task(tid)                    # status_changed (Completed)

    kinds = _kinds(eng.get_task_activity(tid))
    # created is first; then each mutation left exactly its narrative beat.
    assert kinds[0] == "created"
    assert "status_changed" in kinds
    assert "edited" in kinds
    assert "delegated" in kinds
    assert "held" in kinds
    # A single delegation is ONE line, not the ~4 field diffs it wrote to history.
    assert kinds.count("delegated") == 1


def test_delegate_is_one_line_but_multiple_history_rows(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t2"]["id"]
    eng.delegate_task(tid, "steward")
    conn = eng._get_adapter()._conn
    hist = conn.execute(
        "SELECT count(*) FROM entity_history WHERE entity_id = ?", (tid,)
    ).fetchone()[0]
    acts = [e for e in eng.get_task_activity(tid) if e["kind"] == "delegated"]
    assert hist >= 2          # delegate + held_by (+ maybe status) — the forensic layer
    assert len(acts) == 1     # the narrative layer collapses it to one beat
    assert acts[0]["data"]["agent"] == "steward"


def test_subtask_cascade_narrates_parent(populated_work_env):
    eng = populated_work_env["engine"]
    parent = populated_work_env["t1"]["id"]
    sub = eng.add_subtask(parent, "only child")
    eng.complete_task(sub["id"])   # cascades parent → done
    parent_story = eng.get_task_activity(parent)
    assert any(
        e["kind"] == "status_changed" and "subtask" in e["body"].lower()
        for e in parent_story
    ), _bodies(parent_story)


# ── manual-append guard ──────────────────────────────────────────────────────

def test_manual_append_accepts_appendable_kinds(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    for k in ("comment", "attempt", "proof", "blocked", "unblocked", "linked"):
        out = eng.append_activity(tid, k, f"a {k}", actor="agent:advisor")
        assert out is not None and out["kind"] == k


def test_manual_append_refuses_auto_narration_kinds(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    for k in ("created", "status_changed", "delegated", "held", "edited"):
        with pytest.raises(ValueError):
            eng.append_activity(tid, k, "forged", actor="agent:x")


def test_unknown_kind_rejected(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    with pytest.raises(ValueError):
        eng.append_activity(tid, "nonsense", "x")


# ── event emission ───────────────────────────────────────────────────────────

def test_append_emits_task_activity(populated_work_env, monkeypatch):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    events = []
    monkeypatch.setattr(eng, "_notify_dashboard", lambda e: events.append(e))
    eng.append_activity(tid, "attempt", "tried a fix", data={"branch": "fix/x"},
                        actor="agent:advisor")
    acts = [e for e in events if e.get("action") == "task.activity"]
    assert acts and acts[0]["task_id"] == tid
    assert acts[0]["kind"] == "attempt"
    assert acts[0]["actor"] == "agent:advisor"


# ── backfill (migration 089) ─────────────────────────────────────────────────

def _load_migration():
    path = _ROOT / "core" / "infra" / "migrations" / "089_kanban_phase2.py"
    spec = importlib.util.spec_from_file_location("mig089", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_lands_history_once_and_is_idempotent(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY);"
        "CREATE TABLE entity_history (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " entity_type TEXT, entity_id TEXT, field_name TEXT, old_value TEXT,"
        " new_value TEXT, actor TEXT, actor_type TEXT, timestamp TEXT, session_id TEXT);"
        "INSERT INTO tasks (id) VALUES ('aos#1');"
        "INSERT INTO entity_history (entity_type, entity_id, field_name, old_value, new_value, actor, actor_type, timestamp)"
        " VALUES ('task','aos#1','status','todo','active','operator','operator','2026-07-20T10:00:00'),"
        "        ('task','aos#1','delegate',NULL,'advisor','operator','operator','2026-07-20T10:05:00'),"
        "        ('task','aos#1','title','Old','New','operator','operator','2026-07-20T10:06:00');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("AOS_WORK_DB", str(db))
    mig = _load_migration()

    assert mig.check() is False       # not yet applied
    assert mig.up() is True
    assert mig.check() is True        # applied

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT kind, body, source_event_id FROM task_activity ORDER BY id"
    ).fetchall()
    kinds = [r[0] for r in rows]
    assert kinds == ["status_changed", "delegated", "edited"]
    assert all(r[2] and r[2].startswith("history:") for r in rows)
    count_first = len(rows)
    conn.close()

    # Re-run: idempotent — no duplicate narrative rows.
    assert mig.up() is True
    conn = sqlite3.connect(str(db))
    count_second = conn.execute("SELECT count(*) FROM task_activity").fetchone()[0]
    conn.close()
    assert count_second == count_first


# ── API governed action ──────────────────────────────────────────────────────

def test_api_append_action_writes_through_adapter(populated_work_env):
    from core.qareen.actions.work import append_activity as append_action
    from core.qareen.ontology.types import ObjectType

    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    adapter = eng._get_adapter()

    class _Ont:
        _adapters = {ObjectType.TASK: adapter}

    out = asyncio.run(append_action(
        _Ont(), tid, "attempt", "api attempt",
        data={"branch": "fix/api"}, actor="agent:advisor",
    ))
    assert out["task_id"] == tid and out["kind"] == "attempt"
    story = adapter.list_activity(tid)
    assert any(e["kind"] == "attempt" and e["body"] == "api attempt" for e in story)


# ── the islah narrative fixture (qg#1-shaped, FAKE data) ─────────────────────

def test_islah_shaped_story_reads_coherently_and_nothing_flattened(work_env):
    """The acceptance gate for the Phase 3 bugs.yaml import.

    A single bug walks the full islah loop; the timeline must read as a coherent
    story AND the attempt/proof richness (branch, commits, test results) must
    survive verbatim — never flattened into field diffs.
    """
    eng = work_env["engine"]
    eng.add_project("Quran Garden", short_id="qg", project_id="qg")

    # created — filed by the ASC crash intake (Phase 3's ascbuild), stage=new.
    bug = eng.add_task(
        "Last ayah page clips on long surahs",
        project="qg", pipeline="bug", stage="new",
        fields={"severity": 2, "app": "example-app", "build": "1042", "screen": "Reader"},
        source="ascbuild",
    )
    tid = bug["id"]

    # triaged — an investigation attempt confirms the root cause in code.
    eng.update_task(tid, stage="triaging")
    eng.append_activity(
        tid, "attempt", "Investigated — off-by-one in ayah pagination",
        data={"root_cause": "Paginator clamps to count, not count-1",
              "code_refs": ["Sources/Reader/Paginator.swift:142"]},
        actor="agent:advisor",
    )
    eng.update_task(tid, stage="confirmed", status="todo")

    # delegated — handed to a fix agent (assigned_to untouched).
    eng.delegate_task(tid, "advisor")

    # attempt — the fix, with branch + commits.
    eng.append_activity(
        tid, "attempt", "Attempt 1 — clamp to count-1, add boundary test",
        data={"branch": "fix/qg-1-paginator-clamp", "commits": ["abc123", "def456"],
              "outcome": "build_pass"},
        actor="agent:advisor",
    )
    # proof — build + tests, fail-before → pass-after.
    eng.append_activity(
        tid, "proof", "PaginatorTests.lastPage: fail → pass",
        data={"kind": "test", "name": "PaginatorTests.lastPage",
              "before": "fail", "after": "pass", "build": "pass"},
        actor="agent:advisor",
    )
    # in review — awaiting the operator's approval (islah's awaiting-approval).
    eng.update_task(tid, stage="awaiting-approval", status="in_review")

    story = eng.get_task_activity(tid)
    kinds = _kinds(story)

    # The narrative is a coherent, ordered story.
    assert kinds[0] == "created"
    assert kinds.count("attempt") == 2
    assert "proof" in kinds
    assert "delegated" in kinds
    # Ends in review.
    assert story[-1]["kind"] == "status_changed"
    assert story[-1]["data"]["to"] == "in_review"

    # NOTHING flattened — the attempt/proof payloads survive verbatim.
    attempts = [e for e in story if e["kind"] == "attempt"]
    fix = next(e for e in attempts if "Attempt 1" in e["body"])
    assert fix["data"]["branch"] == "fix/qg-1-paginator-clamp"
    assert fix["data"]["commits"] == ["abc123", "def456"]
    proof = next(e for e in story if e["kind"] == "proof")
    assert proof["data"]["before"] == "fail" and proof["data"]["after"] == "pass"

    # The bug-class richness on the task itself is likewise unflattened.
    read = eng.get_task(tid)
    assert read["stage"] == "awaiting-approval"
    assert read["status"] == "in_review"
    assert read["fields"]["app"] == "example-app"

    # The board card summary reflects the latest beat.
    assert read["activity_count"] == len(story)
    assert read["last_activity"]["kind"] == "status_changed"
