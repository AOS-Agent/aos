"""Gate execution for the Git/Ship cockpit.

The "Run gates" engine: runs the three ship gates in the BACKGROUND and streams
progress to the EventBus (→ /api/stream → the browser) so the cockpit's gate chips
flip ``running → pass/warn/fail`` live, then stamps the results into the plan yaml
keyed by the HEAD they ran against (so the UI can mark them STALE when HEAD moves).

INVARIANTS (same spirit as runner.py):
  * Gates VERIFY — they never mutate git, never push, never merge. tsc and
    ship-check are read-only validators; migration-safety is a static lint.
  * Every run is timeout-bounded and the child is killed on overrun.
  * One run per (project, branch) at a time; concurrent POSTs are coalesced.

Result shape matches the seed gate dict (id/scope/status/summary/exit_code/
ran_at/ran_against/source) so it flows straight through the ledger.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# tsc/ship-check on the external SSD can be slow — generous but bounded.
GATE_TIMEOUT = 300.0
_MAX_OUTPUT = 4000  # cap captured output so an event payload can't balloon

# One run per (project, branch); a shared lock serializes plan writes per key.
_RUNNING: set[str] = set()
_PLAN_LOCKS: dict[str, asyncio.Lock] = {}

GATE_IDS = ("tsc", "ship-check", "migration-safety")


# ---------------------------------------------------------------------------
# Bounded subprocess
# ---------------------------------------------------------------------------


async def _run(cmd: list[str], cwd: Path, timeout: float = GATE_TIMEOUT) -> tuple[int, str]:
    """Run a command bounded by ``timeout``; kill the child on overrun.

    Returns ``(exit_code, combined_output)``. 124 signals a timeout kill.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        return (127, f"command not found: {exc}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        return (124, f"timed out after {int(timeout)}s")
    text = out.decode("utf-8", "replace")
    if len(text) > _MAX_OUTPUT:
        text = text[-_MAX_OUTPUT:]
    return (proc.returncode if proc.returncode is not None else -1, text)


# ---------------------------------------------------------------------------
# The three gates
# ---------------------------------------------------------------------------


async def _gate_tsc(repo: Path) -> tuple[int, str]:
    """`tsc -b` over the Qareen frontend. Exit 0 = clean."""
    code, out = await _run(["npx", "tsc", "-b"], repo / "core" / "qareen" / "screen")
    if code == 0:
        return 0, "clean"
    errors = sum(1 for ln in out.splitlines() if ": error TS" in ln)
    return code, f"{errors} type error{'s' if errors != 1 else ''}" if errors else "build failed"


async def _gate_ship_check(repo: Path) -> tuple[int, str]:
    """The AOS ship quality gate. Exit 0 = pass, 2 = warnings, 1 = failures."""
    code, out = await _run(["bash", "core/bin/cli/ship-check"], repo)
    if code == 0:
        return 0, "all checks passed"
    if code == 2:
        return 2, "passed with warnings"
    # Surface the first failing line if we can find one.
    fail_line = next(
        (ln.strip() for ln in out.splitlines() if "FAIL" in ln or "✗" in ln or "error" in ln.lower()),
        None,
    )
    return code, (fail_line[:120] if fail_line else "failures found")


async def _gate_migration_safety(repo: Path) -> tuple[int, str]:
    """Static lint: every migration imports cleanly and the runner accepts the
    tolerant up/apply/run entrypoint (the blocker that silently broke machines).

    Pure read: it byte-compiles the migration files and greps the runner. No DB,
    no execution of any migration.
    """
    mig_dir = repo / "core" / "infra" / "migrations"
    if not mig_dir.exists():
        return 1, "migrations dir missing"

    # 1. Every migration byte-compiles (catches syntax/indent breakage pre-ship).
    code, out = await _run(
        ["python3", "-m", "py_compile", *[str(p) for p in sorted(mig_dir.glob("*.py"))]],
        repo,
        timeout=60.0,
    )
    if code != 0:
        bad = next((ln.strip() for ln in out.splitlines() if ".py" in ln), "compile error")
        return 1, f"migration won't compile: {bad[:90]}"

    # 2. The runner tolerates up/apply/run (blocker #1 — must stay fixed).
    runner_py = mig_dir / "runner.py"
    try:
        text = runner_py.read_text("utf-8", "replace") if runner_py.exists() else ""
    except Exception:
        text = ""
    has_tolerant = all(tok in text for tok in ("up", "apply", "run")) or "getattr" in text
    if runner_py.exists() and not has_tolerant:
        return 1, "runner entrypoint not tolerant (up/apply/run)"

    n = len(list(mig_dir.glob("*.py")))
    return 0, f"{n} migrations compile · entrypoint tolerant"


_GATE_FNS = {
    "tsc": _gate_tsc,
    "ship-check": _gate_ship_check,
    "migration-safety": _gate_migration_safety,
}


def _status_from_code(gate_id: str, code: int) -> str:
    if code == 124:
        return "fail"  # timeout
    if gate_id == "ship-check":
        return "pass" if code == 0 else ("warn" if code == 2 else "fail")
    return "pass" if code == 0 else "fail"


# ---------------------------------------------------------------------------
# Orchestration — background run with live streaming
# ---------------------------------------------------------------------------


async def _emit(bus, project_id: str, gate_id: str, status: str, **extra) -> None:
    if bus is None:
        return
    try:
        from qareen.events.types import Event

        await bus.emit(
            Event(
                event_type="gate.progress",
                source="cockpit",
                payload={"project_id": project_id, "gate": gate_id, "status": status, **extra},
            )
        )
    except Exception:
        logger.debug("gate event emit failed", exc_info=True)


def is_running(project_id: str, branch: str) -> bool:
    return f"{project_id}:{branch}" in _RUNNING


async def run_gates(
    *,
    project_id: str,
    repo: Path,
    branch: str,
    head: str,
    bus,
    store,
    plan: dict,
) -> dict:
    """Run all gates concurrently in the foreground of a background task.

    Streams a ``running`` then a terminal event per gate, persisting each result
    into ``plan['gates']`` under a per-key lock and saving. ``plan`` must already
    be persisted by the caller so reload-modify-save is coherent. Returns the
    final results map. Coalesces concurrent runs via the per-key RUNNING guard.
    """
    key = f"{project_id}:{branch}"
    if key in _RUNNING:
        return {"already_running": True}
    _RUNNING.add(key)
    lock = _PLAN_LOCKS.setdefault(key, asyncio.Lock())

    await _emit(bus, project_id, "*", "running", ran_against=head)

    async def _one(gate_id: str) -> dict:
        await _emit(bus, project_id, gate_id, "running", ran_against=head)
        t0 = time.monotonic()
        fn = _GATE_FNS[gate_id]
        try:
            code, summary = await fn(repo)
        except Exception as exc:  # never let one gate kill the run
            logger.exception("gate %s crashed", gate_id)
            code, summary = -1, f"runner error: {exc}"[:120]
        status = _status_from_code(gate_id, code)
        result = {
            "id": gate_id,
            "scope": "plan",
            "status": status,
            "summary": summary,
            "exit_code": code,
            "ran_at": int(time.time()),
            "ran_against": head,
            "source": "run",
        }
        # Persist this gate's result; reload under lock so concurrent gates don't clobber.
        async with lock:
            try:
                current = store.load(project_id, branch) or plan
                current.setdefault("gates", {})[gate_id] = result
                store.save(project_id, branch, current)
            except Exception:
                logger.exception("failed to persist gate %s", gate_id)
        await _emit(
            bus,
            project_id,
            gate_id,
            status,
            summary=summary,
            exit_code=code,
            ran_at=result["ran_at"],
            ran_against=head,
            duration=round(time.monotonic() - t0, 1),
        )
        return result

    try:
        results = await asyncio.gather(*(_one(g) for g in GATE_IDS))
    finally:
        _RUNNING.discard(key)

    by_id = {r["id"]: r for r in results}
    await _emit(bus, project_id, "*", "done", ran_against=head)
    return by_id
