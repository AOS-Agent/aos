"""Kanban Phase 0 — "make the board honest" backend regression tests.

Covers the truth repairs:
  * entity_history is written on every task status/field mutation (the audit
    trail that /api/tasks/{id}/activity reads).
  * board_tasks() returns the honest working set — old-but-active tasks are
    NOT truncated away (the "0 active" bug).
  * summary() reports authoritative whole-table counts.
  * inbox provenance (source) survives, items can be snoozed and promoted.
  * update_task emits task.status_changed carrying updated_from.
  * ActionRegistry builds TYPED events (task_id/project first-class), the fix
    that lets listeners and GitHub sync fire on the API path.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the `qareen` package importable (package root is core/).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core"))


# ── entity_history / audit trail ────────────────────────────────────────────

def test_status_change_records_history(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]

    eng.start_task(tid)
    eng.update_task(tid, priority=1)
    eng.update_task(tid, status="waiting")
    eng.complete_task(tid)

    conn = eng._get_adapter()._conn
    rows = conn.execute(
        "SELECT field_name, old_value, new_value, actor_type FROM entity_history "
        "WHERE entity_id = ? ORDER BY id",
        (tid,),
    ).fetchall()
    changes = [(r["field_name"], r["old_value"], r["new_value"]) for r in rows]
    assert ("status", "todo", "active") in changes
    assert ("priority", "2", "1") in changes
    assert ("status", "active", "waiting") in changes
    assert ("status", "waiting", "done") in changes
    # actor_type is constrained to the allowed enum values
    assert all(r["actor_type"] in ("operator", "agent", "system", "automation") for r in rows)


def test_no_history_when_value_unchanged(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    eng.update_task(tid, priority=2)  # same as seeded priority
    conn = eng._get_adapter()._conn
    n = conn.execute(
        "SELECT count(*) FROM entity_history WHERE entity_id = ? AND field_name = 'priority'",
        (tid,),
    ).fetchone()[0]
    assert n == 0


# ── board honesty: old active tasks are not truncated ───────────────────────

def test_board_tasks_keeps_old_active(work_env):
    eng = work_env["engine"]
    adapter = eng._get_adapter()
    # One old active task, then a flood of newer todo tasks.
    old = eng.add_task("Old active task")
    eng.start_task(old["id"])
    conn = adapter._conn
    conn.execute("UPDATE tasks SET created_at = '2020-01-01T00:00:00' WHERE id = ?", (old["id"],))
    conn.commit()
    for i in range(50):
        eng.add_task(f"newer todo {i}")

    board = adapter.board_tasks()
    ids = {t.id for t in board}
    assert old["id"] in ids, "an old active task must never be truncated off the board"
    assert any(t.status.value == "active" for t in board)


def test_summary_is_authoritative(populated_work_env):
    eng = populated_work_env["engine"]
    eng.complete_task(populated_work_env["t2"]["id"])
    by_status = eng.summary()["by_status"]
    conn = eng._get_adapter()._conn
    expected = {
        r["status"]: r["cnt"]
        for r in conn.execute(
            "SELECT status, count(*) cnt FROM tasks WHERE parent_id IS NULL GROUP BY status"
        )
    }
    assert by_status == expected


# ── inbox provenance + triage ───────────────────────────────────────────────

def test_inbox_source_persists(work_env):
    eng = work_env["engine"]
    eng.add_inbox("You committed: send the deck [comms 2026-07-12 · src im-42]",
                  source="ambient-commitment")
    item = eng.get_inbox()[0]
    assert item["source"] == "ambient-commitment"


def test_inbox_snooze_hides_then_promote(work_env):
    eng = work_env["engine"]
    item = eng.add_inbox("Draft the pricing note")
    eng.snooze_inbox(item["id"], "2099-01-01T00:00:00")
    assert eng.get_inbox() == []  # snoozed into the future → hidden

    # A fresh item can be promoted to a task and leaves the inbox.
    item2 = eng.add_inbox("Ship the board fix", source="manual")
    task = eng.promote_inbox(item2["id"], project=None, priority=2)
    assert task["title"] == "Ship the board fix"
    assert task["priority"] == 2
    remaining = {i["id"] for i in eng.get_inbox()}
    assert item2["id"] not in remaining


# ── SSE liveness: update_task emits task.status_changed ─────────────────────

def test_update_task_emits_status_changed(populated_work_env, monkeypatch):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    sent: list[dict] = []
    monkeypatch.setattr(eng, "_notify_dashboard", lambda ev: sent.append(ev))

    eng.update_task(tid, status="waiting")
    status_events = [e for e in sent if e.get("action") == "task.status_changed"]
    assert len(status_events) == 1
    ev = status_events[0]
    assert ev["task_id"] == tid
    assert ev["status"] == "waiting"
    assert ev["updated_from"] == {"status": "todo"}

    # A non-status update must not emit a status_changed event.
    sent.clear()
    eng.update_task(tid, priority=1)
    assert not [e for e in sent if e.get("action") == "task.status_changed"]


# ── ActionRegistry emits TYPED events (the §2.4 fix) ────────────────────────

def _make_registry():
    from qareen.events.actions import ActionRegistry
    from qareen.events.audit import AuditLog
    from qareen.events.bus import EventBus

    class _MemAudit(AuditLog):
        def __init__(self):
            pass
        async def log(self, entry):
            return None

    bus = EventBus()
    return ActionRegistry(bus=bus, audit_log=_MemAudit()), bus


def test_action_registry_builds_typed_task_created():
    from qareen.events.actions import ActionDefinition
    from qareen.events.types import TaskCreated

    registry, _ = _make_registry()
    definition = ActionDefinition(name="create_task", emits="task.created")
    result = {"task_id": "aos#9", "title": "Wire the board", "project": "aos"}
    event = registry._build_event(definition, result)
    assert isinstance(event, TaskCreated)
    assert event.task_id == "aos#9"
    assert event.project == "aos"   # getattr(event, "project") now works → github sync fires
    assert event.title == "Wire the board"


def test_action_registry_status_changed_from_updated():
    from qareen.events.actions import ActionDefinition

    registry, _ = _make_registry()
    definition = ActionDefinition(name="update_task", emits="task.updated")
    result = {
        "task_id": "aos#9",
        "updated_fields": ["status"],
        "status": "active",
        "updated_from": {"status": "todo"},
    }
    extra = registry._build_status_changed(definition, result)
    assert extra is not None
    assert extra.event_type == "task.status_changed"
    assert extra.payload["updated_from"] == {"status": "todo"}
