"""Kanban Phase 1 — typed states, delegation, and the bug task-class.

Covers:
  * category mapping — all 13 islah bug stages land on the correct six-value
    category and coarse board status, seeded into the statuses table.
  * transition guard — an unknown status is rejected (no more free-text drift).
  * delegation as a state transition — delegate sets held_by + moves the task
    into a started stage, leaves assigned_to (the accountable human) untouched,
    records entity_history, and emits task.delegated; hold reverts it.
  * bug-class round-trip — a qg#1-shaped bug (FAKE data) with full richness is
    read back unflattened; its awaiting-approval stage maps to in_review.
  * apps registry — the instance override resolves over the framework template.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the `core` package importable (package root is the repo root's parent of core).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core"))

from core.engine.work.pipelines import (  # noqa: E402
    BUG_STAGES,
    bug_stage_category,
    bug_stage_to_status,
)


# ── category mapping ─────────────────────────────────────────────────────────

# The locked mapping (dossier §6 Phase 1 + spec §3.2). stage → (category, coarse).
_EXPECTED = {
    "new":               ("triage",    "triage"),
    "triaging":          ("triage",    "triage"),
    "needs-info":        ("started",   "waiting"),
    "confirmed":         ("unstarted", "todo"),
    "needs-decision":    ("started",   "waiting"),
    "fixing":            ("started",   "active"),
    "verifying":         ("started",   "active"),
    "awaiting-approval": ("started",   "in_review"),
    "approved":          ("completed", "done"),
    "shipped":           ("completed", "done"),
    "reopened":          ("unstarted", "todo"),
    "duplicate":         ("cancelled", "cancelled"),
    "wont-fix":          ("cancelled", "cancelled"),
}


def test_all_13_islah_states_map_correctly():
    assert len(BUG_STAGES) == 13
    for stage, (category, coarse) in _EXPECTED.items():
        assert bug_stage_category(stage) == category, stage
        assert bug_stage_to_status(stage) == coarse, stage
    # Every seeded category is a valid six-value enum member.
    valid = {"triage", "backlog", "unstarted", "started", "completed", "cancelled"}
    assert {s[2] for s in BUG_STAGES} <= valid


def test_bug_stages_seeded_into_statuses(work_env):
    adapter = work_env["engine"]._get_adapter()
    rows = adapter._conn.execute(
        "SELECT id, category FROM statuses WHERE pipeline = 'bug'"
    ).fetchall()
    seeded = {r[0]: r[1] for r in rows}
    assert len(seeded) == 13
    assert seeded["bug:fixing"] == "started"
    assert seeded["bug:awaiting-approval"] == "started"
    assert seeded["bug:duplicate"] == "cancelled"


def test_in_review_generic_status_present(work_env):
    adapter = work_env["engine"]._get_adapter()
    row = adapter._conn.execute(
        "SELECT category FROM statuses WHERE id = 'in_review' AND (pipeline IS NULL OR pipeline='')"
    ).fetchone()
    assert row is not None and row[0] == "started"


# ── transition guard ─────────────────────────────────────────────────────────

def test_illegal_status_rejected(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    with pytest.raises(ValueError):
        eng.update_task(tid, status="cooking")  # not a known board status


def test_valid_status_accepted(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    out = eng.update_task(tid, status="in_review")  # newly-valid Phase 1 status
    assert out["status"] == "in_review"


# ── delegation as a state transition ─────────────────────────────────────────

def test_delegate_sets_holder_and_starts(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    out = eng.delegate_task(tid, "advisor")
    assert out["delegate"] == "advisor"
    assert out["held_by"] == "agent:advisor"
    assert out["status"] == "active"          # moved into a started stage
    assert out.get("assigned_to") in (None, "")  # human accountability untouched


def test_delegate_emits_event_and_history(populated_work_env, monkeypatch):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t2"]["id"]
    events = []
    monkeypatch.setattr(eng, "_notify_dashboard", lambda e: events.append(e))

    eng.delegate_task(tid, "steward", by="operator")

    delegated = [e for e in events if e.get("action") == "task.delegated"]
    assert delegated, "task.delegated must be emitted"
    ev = delegated[0]
    assert ev["task_id"] == tid
    assert ev["holder"] == "agent:steward"
    assert ev["by"] == "operator"
    assert "ts" in ev

    conn = eng._get_adapter()._conn
    fields = {
        r[0] for r in conn.execute(
            "SELECT field_name FROM entity_history WHERE entity_id = ?", (tid,)
        ).fetchall()
    }
    assert {"delegate", "held_by"} <= fields


def test_hold_reverts_holder(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t3"]["id"]
    eng.delegate_task(tid, "advisor")
    out = eng.hold_task(tid)
    assert out["held_by"] == "operator"
    assert "delegate" not in out or out.get("delegate") in (None, "")


# ── bug-class round-trip (qg#1-shaped, FAKE data) ────────────────────────────

def test_bug_class_roundtrip_unflattened(work_env):
    eng = work_env["engine"]
    eng.add_project("Quran Garden", short_id="qg", project_id="qg")

    # A qg#1-shaped fixture with FAKE data: full islah richness on a bug task
    # sitting in awaiting-approval with an in-flight branch + fail-before test.
    richness = {
        "root_cause": "Off-by-one in ayah pagination clamps the last page.",
        "code_refs": ["Sources/Reader/Paginator.swift:142", "Sources/Reader/PageView.swift:88"],
        "fix_approach": "Clamp to count-1 and add a regression test at the boundary.",
        "severity": 2,
        "app": "example-app",
        "build": "1042",
        "screen": "Reader",
        "branch": "fix/qg-1-paginator-clamp",
        "attempts": [{"n": 1, "result": "build_pass", "test": "fail_before_fix"}],
        "proof": [{"kind": "test", "name": "PaginatorTests.lastPage", "before": "fail", "after": "pass"}],
    }
    bug = eng.add_task(
        "Last ayah page clips on long surahs",
        project="qg",
        pipeline="bug",
        stage="awaiting-approval",
        fields=richness,
        source="ascbuild",
    )

    read = eng.get_task(bug["id"])
    assert read["pipeline"] == "bug"
    assert read["stage"] == "awaiting-approval"
    assert read["status"] == "in_review"          # coarse status derived from stage
    got = read["fields"]
    # Nothing flattened — nested structure survives the round-trip verbatim.
    assert got["code_refs"] == richness["code_refs"]
    assert got["attempts"][0]["test"] == "fail_before_fix"
    assert got["proof"][0]["after"] == "pass"
    assert got["branch"] == "fix/qg-1-paginator-clamp"
    assert got["severity"] == 2
    assert got["app"] == "example-app"


# ── apps registry: instance override wins ────────────────────────────────────

def test_apps_registry_instance_override(tmp_path, monkeypatch):
    from core.engine.work import apps_registry

    cfg = tmp_path / "apps.yaml"
    cfg.write_text(
        "apps:\n"
        "  qg:\n"
        "    name: Quran Garden\n"
        "    repo: /Volumes/AOS-X/project/quran-garden\n"
        "    scheme: QuranGarden\n"
        "    bundle_id: com.fake.qurangarden\n"
    )
    monkeypatch.setenv("AOS_CONFIG_DIR", str(tmp_path))

    entry = apps_registry.get_app("qg")
    assert entry is not None
    assert entry.repo == "/Volumes/AOS-X/project/quran-garden"
    assert entry.scheme == "QuranGarden"
    # The framework template's example-app must NOT leak in when an instance
    # override is present (override replaces the map wholesale).
    assert apps_registry.get_app("example-app") is None
