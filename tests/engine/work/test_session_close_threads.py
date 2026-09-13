"""SessionEnd hook regression test: no thread flood (aos#223).

core/engine/work/session_close.py is wired to Claude Code's SessionEnd hook
(core/infra/reconcile/checks/hooks.py). Every session that ends with a cwd
under ~/aos, ~/nuchay or ~/chief-ios-app (or any subdirectory) used to call
``engine.get_or_create_thread_for_cwd(cwd, session_id)`` and mint a brand new
"Work in <dir>" thread every single time — find_thread_by_cwd was a
permanent no-op. Since ~/aos is a release symlink whose target changes on
every update, this fired continuously: 4,635 rows in the live DB, all
status='exploring'.

This test drives the real hook entrypoint (main()) twice for the same
directory with different session ids — the exact shape of two SessionEnds in
one long-lived checkout — and asserts the DB ends up with exactly one
"Work in ..." thread, not two.

Never touches ~/.aos or the real work.db: HOME is monkeypatched to tmp_path
(so session_close.py's known_project_dirs gate matches a directory under
tmp_path instead of the operator's real ~/aos), and the work_env fixture
points AOS_WORK_DB / the cached backend module at a throwaway DB.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

SESSION_CLOSE_PATH = (
    Path(__file__).parents[3] / "core" / "engine" / "work" / "session_close.py"
)


def _load_session_close_fresh():
    """Load session_close.py under a private module name.

    LOG_DIR/LOG_FILE are module-level constants resolved from Path.home() at
    import time, so a cached import from an earlier test (real HOME) would
    leak the operator's real ~/.aos/logs path. A fresh load lets us overwrite
    those constants before main() ever runs, the same way
    tests/test_migration_runner.py isolates runner.py.
    """
    spec = importlib.util.spec_from_file_location(
        "session_close_test", SESSION_CLOSE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_hook_once(mod, monkeypatch, *, cwd: str, session_id: str):
    hook_input = {"session_id": session_id, "cwd": cwd, "transcript": ""}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    mod.main()


def test_two_session_ends_in_same_directory_create_one_thread(work_env, monkeypatch, tmp_path):
    # known_project_dirs is built from Path.home() fresh on every main() call,
    # so redirecting HOME is enough to make a tmp_path directory qualify.
    monkeypatch.setenv("HOME", str(tmp_path))

    project_dir = tmp_path / "aos"
    project_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_session_close_fresh()
    log_dir = tmp_path / ".aos" / "logs"
    monkeypatch.setattr(mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(mod, "LOG_FILE", log_dir / "sessions.jsonl")

    _run_hook_once(mod, monkeypatch, cwd=str(project_dir), session_id="hook-session-1")
    _run_hook_once(mod, monkeypatch, cwd=str(project_dir), session_id="hook-session-2")

    eng = work_env["engine"]
    threads = [t for t in eng.get_all_threads() if t["title"].startswith("Work in ")]
    assert len(threads) == 1, (
        f"two SessionEnds in the same directory produced {len(threads)} "
        f"'Work in ...' threads, expected 1: {threads}"
    )
    assert threads[0]["title"] == "Work in aos"


def test_ten_session_ends_in_same_directory_create_one_thread(work_env, monkeypatch, tmp_path):
    """The real flood shape: many sessions over time in one long-lived checkout."""
    monkeypatch.setenv("HOME", str(tmp_path))

    project_dir = tmp_path / "aos"
    project_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_session_close_fresh()
    log_dir = tmp_path / ".aos" / "logs"
    monkeypatch.setattr(mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(mod, "LOG_FILE", log_dir / "sessions.jsonl")

    for i in range(10):
        _run_hook_once(mod, monkeypatch, cwd=str(project_dir), session_id=f"hook-session-{i}")

    eng = work_env["engine"]
    threads = [t for t in eng.get_all_threads() if t["title"].startswith("Work in ")]
    assert len(threads) == 1


def test_session_end_outside_known_project_dirs_creates_no_thread(work_env, monkeypatch, tmp_path):
    """Directories outside known_project_dirs never got a thread before this
    fix and must not gain one now — the fix is dedup, not new scope."""
    monkeypatch.setenv("HOME", str(tmp_path))

    other_dir = tmp_path / "some-scratch-project"
    other_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_session_close_fresh()
    log_dir = tmp_path / ".aos" / "logs"
    monkeypatch.setattr(mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(mod, "LOG_FILE", log_dir / "sessions.jsonl")

    _run_hook_once(mod, monkeypatch, cwd=str(other_dir), session_id="hook-session-1")

    eng = work_env["engine"]
    threads = [t for t in eng.get_all_threads() if t["title"].startswith("Work in ")]
    assert len(threads) == 0
