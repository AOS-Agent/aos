"""Kanban Phase 4 — the generic runner.

Proves the runner turns a delegation into a spawned worker, safely:

  * event/poll → spawn wiring (the spawn primitive is MOCKED — no test ever
    launches a real `claude`).
  * never auto-completes: a finished worker parks the task in a review state,
    a human approves (spec §3).
  * bounded concurrency: at most `max_concurrent` workers run at once (§3.7).
  * process-group teardown: shutdown kills every worker's group — no orphans
    (the enrich §8 lesson), exercised with a REAL child process.
  * trust-floor gating: below the floor, nothing spawns; the task is parked
    "awaiting operator go" (§3.8). always_escalate is a hard floor.
  * idempotency: one delegation = one spawn, even on a re-poll / replayed event.
  * failure narration: a failed / silent worker parks the task needs-attention,
    never silently dropped.
  * kill switch: enabled=false spawns nothing.
  * declarative worktree isolation, default safe.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never real data,
never a real subprocess except the one explicit orphan-safety test (a harmless
`sleep`).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.engine.work.runner import RunnerConfig, WorkRunner  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg(**over):
    base = dict(enabled=True, max_concurrent=2, spawn_timeout_seconds=30,
                trust_floor=1, default_capability="task_execution")
    base.update(over)
    return RunnerConfig(**base)


def _trust(level=1, agent="advisor", capability="task_execution", escalate=None):
    return {
        "agents": {agent: {"capabilities": {capability: level}}},
        "always_escalate": escalate or [],
    }


def _delegate(eng, task_id, agent="advisor"):
    return eng.delegate_task(task_id, agent)


class _FakeWorker:
    """Stands in for a spawned claude worker. On communicate it optionally writes
    an activity (simulating the agent narrating) and returns a chosen rc."""

    _counter = 1000

    def __init__(self, eng, env, *, rc=0, produce=True, block: threading.Event | None = None):
        _FakeWorker._counter += 1
        self.pid = _FakeWorker._counter  # a fake, non-existent pid
        self._eng = eng
        self._env = env
        self._rc = rc
        self._produce = produce
        self._block = block

    def communicate(self, brief, timeout):
        if self._block is not None:
            self._block.wait(timeout=timeout)
        if self._produce:
            tid = self._env["AOS_TASK_ID"]
            actor = self._env.get("AOS_ACTOR", "agent:advisor")
            self._eng.append_activity(tid, "attempt", "worker did the work", actor=actor)
        return self._rc


def _fake_spawn_factory(eng, *, rc=0, produce=True, block=None, record=None):
    def spawn(cmd, *, brief, cwd, env, log_path):
        if record is not None:
            record.append({"cmd": cmd, "cwd": cwd, "task": env.get("AOS_TASK_ID")})
        return _FakeWorker(eng, env, rc=rc, produce=produce, block=block)
    return spawn


# ── event/poll → spawn wiring + never-auto-complete ─────────────────────────

def test_delegated_task_spawns_and_parks_for_review(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")

    calls = []
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1), spawn=_fake_spawn_factory(eng, record=calls),
                        inline=True)
    summary = runner.poll_once()

    assert len(summary["spawned"]) == 1
    assert len(calls) == 1
    assert calls[0]["task"] == tid
    # The command is the Sentinel-shaped headless spawn with the delegate persona.
    assert calls[0]["cmd"][:2] == [runner_bin(), "--print"] or "--print" in calls[0]["cmd"]
    assert "--agent" in calls[0]["cmd"] and "advisor" in calls[0]["cmd"]

    task = eng.get_task(tid)
    assert task["status"] == "in_review"          # review state …
    assert task["status"] != "done"               # … NEVER auto-completed

    kinds = [a["kind"] for a in eng.get_task_activity(tid)]
    assert "attempt" in kinds                     # the worker narrated
    # runner narrated pickup + completion as system
    bodies = " ".join(a["body"] for a in eng.get_task_activity(tid))
    assert "picked up" in bodies and "ready for review" in bodies


def runner_bin():
    from core.engine.work.runner import CLAUDE_BIN
    return CLAUDE_BIN


# ── kill switch ─────────────────────────────────────────────────────────────

def test_kill_switch_blocks_all_spawning(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")
    calls = []
    runner = WorkRunner(_cfg(enabled=False), backend_mod=eng,
                        db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=3), spawn=_fake_spawn_factory(eng, record=calls),
                        inline=True)
    summary = runner.poll_once()
    assert summary["spawned"] == []
    assert calls == []


# ── trust-floor gating ──────────────────────────────────────────────────────

def test_below_trust_floor_parks_not_spawns(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")
    calls = []
    runner = WorkRunner(_cfg(trust_floor=2), backend_mod=eng,
                        db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1), spawn=_fake_spawn_factory(eng, record=calls),
                        inline=True)
    summary = runner.poll_once()
    assert summary["spawned"] == []
    assert tid in summary["blocked"]
    assert calls == []                            # nothing spawned
    assert eng.get_task(tid)["status"] == "waiting"  # parked for the operator
    runs = _runs(runner, tid)
    assert runs and runs[0]["state"] == "blocked_trust"


def test_always_escalate_is_a_hard_floor(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    # Task declares a financial capability — hard-blocked regardless of level.
    eng.update_task(tid, fields={"capability": "financial_commitment"})
    _delegate(eng, tid, "advisor")
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust={"agents": {"advisor": {"capabilities": {"financial_commitment": 3}}},
                               "always_escalate": ["financial_commitment"]},
                        spawn=_fake_spawn_factory(eng), inline=True)
    summary = runner.poll_once()
    assert summary["spawned"] == []
    assert tid in summary["blocked"]


# ── idempotency: one delegation = one spawn ─────────────────────────────────

def test_idempotent_one_delegation_one_spawn(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")
    calls = []
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1), spawn=_fake_spawn_factory(eng, record=calls),
                        inline=True)

    runner.poll_once()
    runner.poll_once()                                  # re-poll
    runner.handle_delegation({"task_id": tid})          # simulate a replayed event

    assert len(calls) == 1                              # spawned exactly once
    assert len(_runs(runner, tid)) == 1                 # exactly one ledger row


# ── failure narration → needs-attention ─────────────────────────────────────

def test_failed_worker_parks_needs_attention(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1),
                        spawn=_fake_spawn_factory(eng, rc=1, produce=False), inline=True)
    runner.poll_once()

    assert eng.get_task(tid)["status"] == "waiting"     # parked, not dropped
    runs = _runs(runner, tid)
    assert runs[0]["state"] == "failed"
    assert runs[0]["reason"]
    kinds = [a["kind"] for a in eng.get_task_activity(tid)]
    assert "blocked" in kinds                            # failure was narrated


def test_clean_exit_with_no_output_is_a_failure(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1),
                        spawn=_fake_spawn_factory(eng, rc=0, produce=False), inline=True)
    runner.poll_once()
    assert eng.get_task(tid)["status"] == "waiting"
    assert _runs(runner, tid)[0]["state"] == "failed"


# ── bounded concurrency ─────────────────────────────────────────────────────

def test_concurrency_cap_enforced(populated_work_env):
    eng = populated_work_env["engine"]
    ids = []
    for i in range(3):
        t = eng.add_task(f"cap task {i}", project="aos")
        _delegate(eng, t["id"], "advisor")
        ids.append(t["id"])

    gate = threading.Event()
    calls = []
    runner = WorkRunner(_cfg(max_concurrent=2), backend_mod=eng,
                        db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1),
                        spawn=_fake_spawn_factory(eng, block=gate, record=calls),
                        inline=False)                    # threaded — workers stay live
    try:
        summary = runner.poll_once()
        assert len(summary["spawned"]) == 2              # cap holds
        assert len(runner._workers) == 2
    finally:
        gate.set()                                       # release the blocked workers
        _join_workers(runner)

    # With the first two finished, a fresh poll starts the third.
    summary2 = runner.poll_once()
    assert len(summary2["spawned"]) == 1
    gate.set()
    _join_workers(runner)


# ── process-group teardown: no orphans (REAL child) ─────────────────────────

def test_shutdown_kills_worker_group_no_orphan(populated_work_env):
    eng = populated_work_env["engine"]
    tid = populated_work_env["t1"]["id"]
    _delegate(eng, tid, "advisor")

    spawned_pids = []

    def real_spawn(cmd, *, brief, cwd, env, log_path):
        # A harmless long child in its OWN process group — exactly the shape of a
        # real worker (claude + node grandchild), minus the quota burn.
        proc = subprocess.Popen(["sh", "-c", "sleep 30"], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        spawned_pids.append(proc.pid)
        return _RealWorker(proc)

    runner = WorkRunner(_cfg(spawn_timeout_seconds=30), backend_mod=eng,
                        db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1), spawn=real_spawn, inline=False)
    runner.poll_once()
    assert len(spawned_pids) == 1
    pid = spawned_pids[0]
    assert _alive(pid)                                   # child is running

    runner.shutdown()                                    # tears down all groups
    assert _dead_within(pid, 5.0)                        # child killed, not orphaned
    _join_workers(runner)


class _RealWorker:
    def __init__(self, proc):
        self._proc = proc

    @property
    def pid(self):
        return self._proc.pid

    def communicate(self, brief, timeout):
        self._proc.communicate(timeout=timeout)
        return self._proc.returncode

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)


# ── declarative worktree isolation ──────────────────────────────────────────

def test_isolation_is_declarative_and_default_safe(populated_work_env):
    eng = populated_work_env["engine"]
    runner = WorkRunner(_cfg(), backend_mod=eng, db_path=str(populated_work_env["db_path"]),
                        trust=_trust(level=1), spawn=_fake_spawn_factory(eng), inline=True)
    assert runner.resolve_isolation({"pipeline": None, "fields": None}) == "none"
    assert runner.resolve_isolation({"pipeline": "bug", "fields": None}) == "worktree"
    # An explicit declaration wins over the pipeline default.
    assert runner.resolve_isolation({"pipeline": "bug", "fields": '{"isolation": "none"}'}) == "none"
    assert runner.resolve_isolation({"pipeline": None, "fields": '{"isolation": "worktree"}'}) == "worktree"


# ── small utilities ─────────────────────────────────────────────────────────

def _runs(runner, task_id):
    conn = runner._conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ?", (task_id,)).fetchall()]
    finally:
        conn.close()


def _join_workers(runner, timeout=5.0):
    for w in list(runner._workers.values()):
        if w.thread:
            w.thread.join(timeout=timeout)
    # let the done-queue finalize
    runner._drain_done()


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _dead_within(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)
