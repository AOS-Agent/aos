"""The generic work runner — turns a delegation into a running agent.

Kanban Phase 4 (spec: ~/vault/knowledge/initiatives/agent-driven-kanban.md).
When a task is delegated to an agent (``held_by = 'agent:<name>'``, spec §3.1),
this runner picks it up, assembles a brief, spawns a headless ``claude`` worker
(the Sentinel spawn primitive, §2.1), and lets the worker narrate its progress
into the task's append-only activity log (Phase 2). It NEVER auto-completes a
task — a finished worker leaves the task in a review state for a human (spec §3),
and a failed worker parks it in a needs-attention state, never silently dropped.

This is the DOMAIN-AGNOSTIC runner. It knows about tasks, delegation, trust, and
process hygiene — nothing about iOS, xcodebuild, or DerivedData. The code-task
class (islah's dispatch strategy: union-find file-collision lanes, worktree +
DerivedData isolation, a build/test gate) rides ON TOP of this as a Phase-5
plugin keyed off ``pipeline='bug'``; its guts never leak in here (dossier
risk-2). What this runner DOES inherit generically are the isolation *patterns*:
process-group teardown (proc_group.py), bounded concurrency, and a declarative
worktree seam.

Design commitments honored here (all locked in the spec):
  * §3.4  Poll the board — the board is the queue. Events are an accelerant,
          never the source of truth. ``handle_delegation`` is the optional
          event-driven fast path; ``poll_once`` is the authority. A killed
          runner restarts and resumes from ``work.db`` with no reconciliation.
  * §3.7  Bounded concurrency (default 2), a per-task timeout, process-group
          discipline, graceful degradation — this is a subscription, not an API
          key. No fan-out storms.
  * §3.8  Trust is enforced at the spawn point: a delegated task only auto-spawns
          if the agent's capability trust ≥ the configured floor; below it, the
          task is parked "awaiting operator go", not spawned. ``always_escalate``
          capabilities are a hard floor regardless of level.
  * Kill switch: ``runner.enabled`` ships FALSE. Autonomous agent spawning is
          opt-in per the trust philosophy.
  * Idempotency: one delegation = one spawn. The ``task_runs`` ledger dedupes on
          ``(task_id, delegation_ts)`` so a replayed event or a re-poll never
          double-spawns.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import signal
import sqlite3
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from core.engine.work.proc_group import ProcessGroupRegistry, terminate_group

log = logging.getLogger(__name__)

CLAUDE_BIN = shutil.which("claude") or "claude"

CONFIG_PATH = Path.home() / ".aos" / "config" / "work-runner.yaml"
TRUST_PATH = Path.home() / ".aos" / "config" / "trust.yaml"
LOG_DIR = Path.home() / ".aos" / "logs" / "work-runner"
WORKTREE_ROOT = Path.home() / ".aos" / "work" / "runner" / "worktrees"

# Runner-owned run states (the ledger, distinct from the task's own status —
# the task status is derived from its activity log, spec §3.3; this tracks the
# runner's view of a spawn's lifecycle so it never double-spawns or orphans).
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_REVIEW = "review"          # worker finished → task parked for human review
RUN_FAILED = "failed"          # worker failed → task parked needs-attention
RUN_CANCELLED = "cancelled"    # operator cancelled the run
RUN_BLOCKED_TRUST = "blocked_trust"  # trust floor not met → not spawned

_TERMINAL_RUN_STATES = {RUN_REVIEW, RUN_FAILED, RUN_CANCELLED, RUN_BLOCKED_TRUST}

# Task statuses that mean "no longer needs a runner" — a delegated task already
# in one of these is skipped (someone/something already resolved it).
_CLOSED_TASK_STATUSES = {"done", "cancelled"}


DEFAULTS: dict[str, Any] = {
    "enabled": False,               # KILL SWITCH — autonomous spawning off by default
    "max_concurrent": 2,            # bounded concurrency (spec §3.7)
    "spawn_timeout_seconds": 600,   # per-task wall-clock cap
    "poll_interval_seconds": 15,
    "trust_floor": 1,               # min capability level to auto-spawn (spec §3.8)
    "default_capability": "task_execution",
    "allowed_tools": "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch",
    "model": None,                  # null = the agent's default model
    "review_status": "in_review",   # generic terminal-success state — NEVER 'done'
    "needs_attention_status": "waiting",  # failure / trust-park state
}


@dataclass
class RunnerConfig:
    enabled: bool = False
    max_concurrent: int = 2
    spawn_timeout_seconds: int = 600
    poll_interval_seconds: int = 15
    trust_floor: int = 1
    default_capability: str = "task_execution"
    allowed_tools: str = DEFAULTS["allowed_tools"]
    model: Optional[str] = None
    review_status: str = "in_review"
    needs_attention_status: str = "waiting"

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "RunnerConfig":
        raw: dict = {}
        if path.exists():
            try:
                raw = yaml.safe_load(path.read_text()) or {}
            except Exception as e:  # noqa: BLE001
                log.warning("work-runner config parse failed (%s); using defaults", e)
        merged = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
        return cls(**merged)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_trust(path: Path = TRUST_PATH) -> dict:
    """Instance trust.yaml, falling back to the framework default so a fresh
    machine still gates. A missing/broken file yields an empty tree → every
    agent reads as untrusted (safe default: nothing auto-spawns)."""
    for p in (path, Path(__file__).resolve().parents[3] / "config" / "defaults" / "trust.yaml"):
        if p.exists():
            try:
                return yaml.safe_load(p.read_text()) or {}
            except Exception:  # noqa: BLE001
                continue
    return {}


@dataclass
class _Worker:
    """A live worker the runner is tracking (in-memory, per-process)."""
    run_id: str
    task_id: str
    agent: str
    proc: Any
    started: float
    deadline: float
    activity_high_water: int
    worktree: Optional[Path] = None
    thread: Optional[threading.Thread] = None


@dataclass
class _Done:
    """A finished worker awaiting single-threaded finalization on the main loop."""
    run_id: str
    task_id: str
    agent: str
    returncode: int
    activity_high_water: int
    worktree: Optional[Path]
    error: Optional[str] = None


TASK_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    delegation_ts TEXT NOT NULL,
    holder        TEXT NOT NULL,
    agent         TEXT NOT NULL,
    state         TEXT NOT NULL,
    pid           INTEGER,
    attempt       INTEGER NOT NULL DEFAULT 1,
    log_path      TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    ended_at      TEXT,
    reason        TEXT,
    UNIQUE(task_id, delegation_ts)
);
CREATE INDEX IF NOT EXISTS idx_task_runs_state ON task_runs(state);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
"""


class WorkRunner:
    """Polls ``work.db`` for delegated tasks and runs agents on them.

    All DB writes happen on ONE thread (the caller of ``poll_once`` / the finalize
    path) — worker threads only run the subprocess and hand a result back through
    a queue, so the SQLite connection is never touched concurrently.
    """

    def __init__(
        self,
        config: Optional[RunnerConfig] = None,
        *,
        db_path: Optional[str] = None,
        backend_mod: Any = None,
        trust: Optional[dict] = None,
        spawn: Optional[Callable[..., Any]] = None,
        registry: Optional[ProcessGroupRegistry] = None,
        inline: bool = False,
    ) -> None:
        self.config = config or RunnerConfig.load()
        if backend_mod is None:
            from core.engine.work import backend as backend_mod  # lazy: heavy import
        self._backend = backend_mod
        self._db_path = str(db_path or self._backend.DB_PATH)
        self._trust = trust if trust is not None else _load_trust()
        self._spawn = spawn or _default_spawn
        self._registry = registry or ProcessGroupRegistry()
        self._inline = inline  # tests: run worker body synchronously, no thread

        self._workers: dict[str, _Worker] = {}
        self._lock = threading.Lock()
        self._done_q: "queue.Queue[_Done]" = queue.Queue()
        self._stop = threading.Event()
        self._ensure_schema()

    # ── DB plumbing ──────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(TASK_RUNS_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ── Enable / kill-switch ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Re-read the config each check so a live ``enabled: false`` edit (the
        operator's global pause) takes effect without a restart."""
        return RunnerConfig.load().enabled

    # ── Candidate selection (the poll) ───────────────────────────────────────

    def _candidate_tasks(self, conn: sqlite3.Connection) -> list[dict]:
        """Delegated tasks that may need a runner: held by an agent and not in a
        closed status. Poll the board (spec §3.4) — this is the source of truth,
        derived fresh every cycle so a crash/restart resumes cleanly."""
        rows = conn.execute(
            "SELECT id, title, description, project_id, status, pipeline, "
            "pipeline_stage, delegate, held_by, fields "
            "FROM tasks WHERE held_by LIKE 'agent:%' "
            "ORDER BY COALESCE(modified_at, created_at) ASC"
        ).fetchall()
        out = []
        for r in rows:
            if (r["status"] or "") in _CLOSED_TASK_STATUSES:
                continue
            out.append(dict(r))
        return out

    def _latest_delegation_ts(self, conn: sqlite3.Connection, task_id: str) -> str:
        """The idempotency key: the ts of the most recent ``delegated`` narration
        for the task. One delegation → one ts → one run row. Falls back to a
        stable per-task marker if the narration is somehow missing so we still
        never double-spawn."""
        row = conn.execute(
            "SELECT ts FROM task_activity WHERE task_id = ? AND kind = 'delegated' "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row and row["ts"]:
            return row["ts"]
        row = conn.execute(
            "SELECT COALESCE(modified_at, started_at, created_at, '?') AS m "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return (row["m"] if row else None) or "unknown"

    def _existing_run(self, conn, task_id: str, delegation_ts: str) -> Optional[dict]:
        row = conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? AND delegation_ts = ?",
            (task_id, delegation_ts),
        ).fetchone()
        return dict(row) if row else None

    def _activity_high_water(self, conn, task_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS hi FROM task_activity WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row["hi"]) if row else 0

    def _worker_produced_output(self, conn, task_id: str, high_water: int, agent: str) -> bool:
        """Did the worker narrate anything past the pickup snapshot? A clean exit
        with zero new activity means the agent did nothing — treat as a failure so
        the task is parked for attention, not silently marked reviewed."""
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM task_activity "
            "WHERE task_id = ? AND id > ? AND actor NOT LIKE 'system:%'",
            (task_id, high_water),
        ).fetchone()
        return bool(row and row["n"] > 0)

    # ── Trust gate (spec §3.8) ───────────────────────────────────────────────

    def _capability_for(self, task: dict) -> Optional[str]:
        """The capability a task exercises, from its ``fields`` JSON if declared,
        else the configured default. Used to look up the agent's trust level."""
        fields = _parse_fields(task.get("fields"))
        cap = fields.get("capability")
        return cap or self.config.default_capability

    def _always_escalate(self) -> set[str]:
        return set(self._trust.get("always_escalate", []) or [])

    def _trust_level(self, agent: str, capability: Optional[str]) -> int:
        """The agent's trust level for a capability. An unknown agent is -1 (below
        any floor). A known agent that doesn't declare the capability is 0 (not
        trusted for it). No capability specified → the agent's highest declared
        level as a general-competence proxy."""
        agents = (self._trust.get("agents") or {})
        a = agents.get(agent)
        if not a:
            return -1
        caps = a.get("capabilities") or {}
        if capability and capability in caps:
            return int(caps[capability])
        if not capability:
            return max((int(v) for v in caps.values()), default=0)
        return 0

    def gate(self, task: dict, agent: str) -> tuple[bool, Optional[str]]:
        """(allowed, reason_if_blocked). ``always_escalate`` capabilities are a
        hard floor regardless of level; otherwise the level must meet the floor."""
        cap = self._capability_for(task)
        if cap in self._always_escalate():
            return False, f"'{cap}' is always-escalate — operator only"
        level = self._trust_level(agent, cap)
        if level < self.config.trust_floor:
            return False, (
                f"{agent} trust {level} for '{cap}' below floor "
                f"{self.config.trust_floor} — awaiting operator go"
            )
        return True, None

    # ── Isolation seam (declarative, default safe) ───────────────────────────

    def resolve_isolation(self, task: dict) -> str:
        """Declarative isolation for a task: 'worktree' or 'none'. A task may
        request it via ``fields.isolation``; the bug pipeline defaults to a
        worktree (code-touching, dossier risk-2); everything else defaults to
        'none'. The generic runner implements the worktree *primitive*; the
        iOS-specific build/DerivedData layer is a Phase-5 plugin on top."""
        fields = _parse_fields(task.get("fields"))
        declared = fields.get("isolation")
        if declared in ("worktree", "none"):
            return declared
        if (task.get("pipeline") or "") == "bug":
            return "worktree"
        return "none"

    # ── Brief assembly ───────────────────────────────────────────────────────

    def build_brief(self, task: dict) -> str:
        """The lean context pack handed to the worker on stdin. Reuses the task's
        handoff prompt + its activity history (Phase 2). Ambient knowledge (QMD)
        is available to the agent through its own tools; the brief stays lean —
        rich context assembly is Phase 7's pre-flight, not baked in here."""
        tid = task["id"]
        lines = [
            "You are picking up a delegated task on the AOS board.",
            "",
            f"TASK {tid}: {task.get('title', '')}",
        ]
        if task.get("project_id"):
            lines.append(f"Project: {task['project_id']}")
        if task.get("description"):
            lines.append("")
            lines.append(task["description"])

        try:
            handoff = self._backend.build_handoff_prompt(tid)
        except Exception:  # noqa: BLE001
            handoff = None
        if handoff:
            lines += ["", "── Handoff ──", handoff]

        try:
            activity = self._backend.get_task_activity(tid, limit=40)
        except Exception:  # noqa: BLE001
            activity = []
        if activity:
            lines += ["", "── Recent activity ──"]
            for a in activity[-12:]:
                lines.append(f"  [{a.get('kind')}] {a.get('body', '')}")

        lines += [
            "",
            "── Your contract ──",
            "1. Do the work the task describes.",
            "2. Narrate progress into the task's activity log as you go:",
            f"     work activity {tid} --kind attempt --body \"…\" --actor agent:{task.get('delegate','')}",
            "   Use kind 'attempt' for a work step, 'proof' for evidence it works,",
            "   'comment' for a note. This is how the operator sees what you did.",
            "3. Do NOT mark the task done. When you finish, stop — the task is left",
            "   in a review state for a human to approve (that is by design).",
            "4. If you are blocked and need the operator, say so via a 'comment'",
            "   and stop.",
        ]
        return "\n".join(lines)

    def build_command(self, agent: str) -> list[str]:
        """The Sentinel-shaped spawn (spec §2.1): headless claude, bypass perms,
        a bounded tool set, the agent persona. Prompt goes on stdin."""
        cmd = [
            CLAUDE_BIN, "--print",
            "--agent", agent,
            "--dangerously-skip-permissions",
            "--allowedTools", self.config.allowed_tools,
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        return cmd

    # ── The poll loop ────────────────────────────────────────────────────────

    def poll_once(self) -> dict:
        """One board scan. Finalize finished workers, enforce timeouts, then spawn
        eligible delegations up to the concurrency cap. Returns a small summary.
        Safe to call repeatedly; correctness never depends on events (§3.4)."""
        summary = {"spawned": [], "finalized": 0, "blocked": [], "skipped": 0}

        # 1. Finalize anything workers have finished (single-threaded DB writes).
        summary["finalized"] = self._drain_done()

        # 2. Enforce per-task timeouts (§3.7) — kill the group; the worker thread
        #    then observes the timeout and enqueues a failure.
        self._enforce_timeouts()

        if self._stop.is_set() or self._registry.shutting_down:
            return summary

        # Kill switch (§ trust philosophy). Uses the runner's current config;
        # run_forever reloads it each tick so an operator's live ``enabled: false``
        # edit takes hold without a restart. Already-running workers finish/park.
        if not self.config.enabled:
            return summary

        # 3. Spawn eligible delegations up to the cap.
        conn = self._conn()
        try:
            candidates = self._candidate_tasks(conn)
            for task in candidates:
                with self._lock:
                    running = len(self._workers)
                if running >= self.config.max_concurrent:
                    break
                tid = task["id"]
                if tid in self._workers:  # already in flight this process
                    continue
                agent = (task.get("delegate") or "").strip()
                if not agent:
                    continue
                deleg_ts = self._latest_delegation_ts(conn, tid)
                if self._existing_run(conn, tid, deleg_ts):
                    summary["skipped"] += 1  # idempotency: one delegation = one run
                    continue

                allowed, reason = self.gate(task, agent)
                if not allowed:
                    self._park_blocked_trust(conn, task, agent, deleg_ts, reason)
                    summary["blocked"].append(tid)
                    continue

                run_id = self._start_worker(conn, task, agent, deleg_ts)
                if run_id:
                    summary["spawned"].append(run_id)
        finally:
            conn.close()
        return summary

    def run_forever(self) -> None:
        """The service loop. Poll on an interval; the board is the queue."""
        restore = self._registry.install_signal_handlers()
        log.info(
            "work-runner loop starting (enabled=%s, cap=%d, interval=%ds)",
            self.config.enabled, self.config.max_concurrent,
            self.config.poll_interval_seconds,
        )
        try:
            while not self._stop.is_set():
                # Reload config each tick so a live `enabled: false` (the operator's
                # global pause) or a knob change takes hold without a restart.
                self.config = RunnerConfig.load()
                try:
                    self.poll_once()
                except Exception as e:  # noqa: BLE001
                    log.exception("poll_once error: %s", e)
                self._stop.wait(self.config.poll_interval_seconds)
        finally:
            restore()
            self.shutdown()

    def handle_delegation(self, payload: dict) -> Optional[str]:
        """Optional event-driven fast path (accelerant, not authority — §3.4).
        A ``task.delegated`` consumer can call this for instant pickup. It is just
        a targeted ``poll_once`` gate for one task; the poll loop is still the
        source of truth and would pick the same task up on its next tick."""
        tid = payload.get("task_id")
        if not tid or not self.config.enabled:
            return None
        # Cheapest correct thing: run a normal poll. It will find this task.
        result = self.poll_once()
        for rid in result.get("spawned", []):
            return rid
        return None

    # ── Spawn + finalize ─────────────────────────────────────────────────────

    def _start_worker(self, conn, task: dict, agent: str, deleg_ts: str) -> Optional[str]:
        """Create the run row, narrate pickup, and launch the worker. Returns the
        run id, or None if the ledger row could not be claimed (a concurrent
        double-spawn lost the race — idempotency held)."""
        tid = task["id"]
        run_id = "run_" + uuid.uuid4().hex[:16]
        log_path = LOG_DIR / f"{run_id}.log"
        holder = task.get("held_by") or f"agent:{agent}"

        # Claim the delegation atomically. UNIQUE(task_id, delegation_ts) means a
        # replayed event or racing poll cannot create a second row.
        try:
            conn.execute(
                "INSERT INTO task_runs "
                "(id, task_id, delegation_ts, holder, agent, state, log_path, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run_id, tid, deleg_ts, holder, agent, RUN_RUNNING,
                 str(log_path), _now()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None  # someone else already claimed this delegation

        high_water = self._activity_high_water(conn, tid)

        # Isolation (declarative, default safe). The worktree primitive is
        # generic; the code-build layer on top is Phase 5.
        worktree = None
        cwd = None
        if self.resolve_isolation(task) == "worktree":
            worktree = self._make_worktree(task, run_id)
            cwd = str(worktree) if worktree else None

        self._narrate(tid, "comment",
                      f"Runner picked up — delegated to {agent}, worker starting.",
                      actor="system:runner",
                      data={"run_id": run_id, "isolation": "worktree" if worktree else "none"})

        brief = self.build_brief(task)
        cmd = self.build_command(agent)
        env = dict(os.environ)
        env["AOS_TASK_ID"] = tid
        env["AOS_RUNNER_RUN_ID"] = run_id
        env["AOS_ACTOR"] = f"agent:{agent}"  # so the worker's `work activity` writes narrate as the agent

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            proc = self._spawn(cmd, brief=brief, cwd=cwd, env=env, log_path=log_path)
        except Exception as e:  # noqa: BLE001
            log.exception("spawn failed for %s: %s", tid, e)
            self._finalize(_Done(run_id, tid, agent, 1, high_water, worktree,
                                 error=f"spawn failed: {e}"))
            return run_id

        self._registry.register(proc.pid)
        import time as _t
        worker = _Worker(
            run_id=run_id, task_id=tid, agent=agent, proc=proc,
            started=_t.monotonic(),
            deadline=_t.monotonic() + self.config.spawn_timeout_seconds,
            activity_high_water=high_water, worktree=worktree,
        )
        with self._lock:
            self._workers[tid] = worker

        conn.execute(
            "UPDATE task_runs SET pid = ?, started_at = ? WHERE id = ?",
            (proc.pid, _now(), run_id),
        )
        conn.commit()

        if self._inline:
            # Test / synchronous mode: run the worker body and finalize now.
            self._run_worker_body(worker, brief)
            self._drain_done()
        else:
            t = threading.Thread(target=self._run_worker_body, args=(worker, brief),
                                 daemon=True, name=f"work-runner-{run_id}")
            worker.thread = t
            t.start()
        return run_id

    def _run_worker_body(self, worker: _Worker, brief: str) -> None:
        """Worker-thread body: run the subprocess to completion, then hand the
        result to the main loop via the done-queue. NO DB writes happen here —
        finalization (which touches SQLite) is single-threaded on the poll loop."""
        proc = worker.proc
        rc = 1
        error = None
        try:
            rc = proc.communicate(brief, timeout=self.config.spawn_timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_group(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            rc = 124
            error = f"timeout after {self.config.spawn_timeout_seconds}s"
        except Exception as e:  # noqa: BLE001
            error = f"worker crashed: {e}"
            rc = 1
        finally:
            self._registry.unregister(proc.pid)
            with self._lock:
                self._workers.pop(worker.task_id, None)
        self._done_q.put(_Done(worker.run_id, worker.task_id, worker.agent,
                               rc if isinstance(rc, int) else 1,
                               worker.activity_high_water, worker.worktree, error))

    def _drain_done(self) -> int:
        n = 0
        while True:
            try:
                done = self._done_q.get_nowait()
            except queue.Empty:
                break
            self._finalize(done)
            n += 1
        return n

    def _finalize(self, done: _Done) -> None:
        """Single-threaded terminal handling: transition the task to a review or
        needs-attention state (NEVER 'done' — spec §3), narrate the outcome, close
        the run row, and tear down the worktree. Idempotent per run row."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT state FROM task_runs WHERE id = ?",
                               (done.run_id,)).fetchone()
            if row and row["state"] in _TERMINAL_RUN_STATES:
                return  # already finalized

            tid = done.task_id
            ok = (done.returncode == 0) and not done.error
            produced = ok and self._worker_produced_output(
                conn, tid, done.activity_high_water, done.agent)

            if ok and produced:
                state, reason = RUN_REVIEW, None
                self._narrate(tid, "comment",
                              f"Agent {done.agent} finished — task ready for review.",
                              actor="system:runner", data={"run_id": done.run_id})
                self._transition(tid, self.config.review_status)
            else:
                state = RUN_FAILED
                if done.error:
                    reason = done.error
                elif not produced:
                    reason = "worker exited without producing any output"
                else:
                    reason = f"worker exited rc={done.returncode}"
                # Failure is narrated and parked — NEVER silently dropped (§ lifecycle).
                self._narrate(tid, "blocked",
                              f"Agent {done.agent} did not complete: {reason}.",
                              actor="system:runner",
                              data={"run_id": done.run_id, "returncode": done.returncode})
                self._transition(tid, self.config.needs_attention_status)

            conn.execute(
                "UPDATE task_runs SET state = ?, ended_at = ?, reason = ? WHERE id = ?",
                (state, _now(), reason, done.run_id),
            )
            conn.commit()
        finally:
            conn.close()
        if done.worktree:
            self._remove_worktree(done.worktree)

    def _park_blocked_trust(self, conn, task, agent, deleg_ts, reason) -> None:
        """Below the trust floor: record the decision (so we don't re-evaluate
        every poll — idempotent on the delegation) and park the task for the
        operator instead of spawning (spec §3.8, L0 semantics)."""
        run_id = "run_" + uuid.uuid4().hex[:16]
        try:
            conn.execute(
                "INSERT INTO task_runs "
                "(id, task_id, delegation_ts, holder, agent, state, created_at, ended_at, reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, task["id"], deleg_ts, task.get("held_by") or f"agent:{agent}",
                 agent, RUN_BLOCKED_TRUST, _now(), _now(), reason),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return
        self._narrate(task["id"], "blocked",
                      f"Held for operator: {reason}.",
                      actor="system:runner", data={"run_id": run_id})
        self._transition(task["id"], self.config.needs_attention_status)

    # ── Task writes (through the sanctioned backend path) ────────────────────

    def _narrate(self, task_id: str, kind: str, body: str, *,
                 actor: str, data: Optional[dict] = None) -> None:
        try:
            self._backend.append_activity(task_id, kind, body, data=data, actor=actor)
        except Exception as e:  # noqa: BLE001
            log.warning("narrate failed for %s: %s", task_id, e)

    def _transition(self, task_id: str, status: str) -> None:
        try:
            self._backend.update_task(task_id, status=status)
        except Exception as e:  # noqa: BLE001
            log.warning("transition failed for %s → %s: %s", task_id, status, e)

    # ── Worktree primitive (generic; no build/DerivedData here) ──────────────

    def _repo_for(self, task: dict) -> Optional[Path]:
        """The git repo a code-touching task works in. Phase 4 keeps this a lean
        seam — a task may name its repo in ``fields.repo``; the per-app registry
        wiring is Phase 5. No repo → no worktree (fall back to running in place)."""
        fields = _parse_fields(task.get("fields"))
        repo = fields.get("repo")
        if repo:
            p = Path(repo).expanduser()
            if (p / ".git").exists():
                return p
        return None

    def _make_worktree(self, task: dict, run_id: str) -> Optional[Path]:
        repo = self._repo_for(task)
        if not repo:
            return None
        WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
        dest = WORKTREE_ROOT / f"{task['id']}-{run_id}"
        branch = f"runner/{task['id']}-{run_id[:8]}"
        try:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(dest), "HEAD"],
                check=True, capture_output=True, text=True, timeout=60,
            )
            return dest
        except Exception as e:  # noqa: BLE001
            log.warning("worktree add failed for %s: %s", task["id"], e)
            return None

    def _remove_worktree(self, dest: Path) -> None:
        try:
            # Find the owning repo from the worktree's .git file if possible; a
            # best-effort prune + force remove keeps the tree clean.
            subprocess.run(["git", "-C", str(dest), "worktree", "remove", "--force", str(dest)],
                           capture_output=True, text=True, timeout=60)
        except Exception:  # noqa: BLE001
            pass
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)

    def _enforce_timeouts(self) -> None:
        import time as _t
        now = _t.monotonic()
        with self._lock:
            over = [w for w in self._workers.values() if now > w.deadline]
        for w in over:
            log.warning("worker %s over deadline — killing group", w.run_id)
            terminate_group(w.proc.pid, signal.SIGKILL)

    # ── Controls ─────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """What's running and queued — the ``work runner status`` view."""
        conn = self._conn()
        try:
            running = [dict(r) for r in conn.execute(
                "SELECT id, task_id, agent, state, pid, started_at "
                "FROM task_runs WHERE state = ? ORDER BY started_at DESC",
                (RUN_RUNNING,)).fetchall()]
            recent = [dict(r) for r in conn.execute(
                "SELECT id, task_id, agent, state, reason, ended_at "
                "FROM task_runs WHERE state != ? ORDER BY COALESCE(ended_at, created_at) DESC LIMIT 15",
                (RUN_RUNNING,)).fetchall()]
        finally:
            conn.close()
        return {
            "enabled": self.config.enabled,
            "max_concurrent": self.config.max_concurrent,
            "in_process_workers": len(self._workers),
            "running": running,
            "recent": recent,
        }

    def cancel(self, task_id: str) -> bool:
        """SIGTERM the worker's process group for a task and mark the run
        cancelled. The worker thread observes the death and parks the task."""
        with self._lock:
            worker = self._workers.get(task_id)
        if worker is None:
            # Not live in this process — mark any running ledger row cancelled so
            # a different runner instance / restart won't treat it as live.
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE task_runs SET state = ?, ended_at = ?, reason = ? "
                    "WHERE task_id = ? AND state = ?",
                    (RUN_CANCELLED, _now(), "cancelled by operator", task_id, RUN_RUNNING),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
        terminate_group(worker.proc.pid, signal.SIGTERM)
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE task_runs SET state = ?, ended_at = ?, reason = ? WHERE id = ?",
                (RUN_CANCELLED, _now(), "cancelled by operator", worker.run_id),
            )
            conn.commit()
        finally:
            conn.close()
        return True

    def shutdown(self) -> None:
        """Tear down every live worker's process group — a killed/redeployed
        runner must never orphan quota-burning children (enrich §8 lesson)."""
        self._stop.set()
        pids = self._registry.terminate_all()
        if pids:
            log.info("shutdown: terminated %d worker group(s)", len(pids))

    def stop(self) -> None:
        self._stop.set()


# ── Spawn primitive (Sentinel-shaped) + test seam ───────────────────────────

def _default_spawn(cmd: list[str], *, brief: str, cwd: Optional[str],
                   env: dict, log_path: Path) -> Any:
    """Launch a headless ``claude`` worker in its OWN process group (so the whole
    group — claude and its node grandchild — is killpg-able). Returns an object
    exposing ``.pid`` and ``.communicate(brief, timeout) -> returncode``. Prompt
    is fed on stdin (the Sentinel convention), output tee'd to ``log_path``."""
    logf = open(log_path, "w")
    logf.write(f"=== command: {' '.join(cmd)} (cwd={cwd}) ===\n")
    logf.flush()
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT,
        text=True, cwd=cwd, env=env, start_new_session=True,
    )
    return _PopenWorker(proc, logf)


class _PopenWorker:
    """Thin adapter so the runner talks to one small interface (real or fake)."""

    def __init__(self, proc: subprocess.Popen, logf) -> None:
        self._proc = proc
        self._logf = logf

    @property
    def pid(self) -> int:
        return self._proc.pid

    def communicate(self, brief: str, timeout: int) -> int:
        try:
            self._proc.communicate(input=brief, timeout=timeout)
        finally:
            try:
                self._logf.close()
            except Exception:  # noqa: BLE001
                pass
        return self._proc.returncode

    def wait(self, timeout: Optional[int] = None) -> int:
        return self._proc.wait(timeout=timeout)

    def poll(self):
        return self._proc.poll()


def _parse_fields(raw: Any) -> dict:
    import json
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
