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
(so session_close.py's legacy-directory fallback matches a directory under
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
    # The legacy-directory fallback is built from Path.home() fresh on every
    # main() call, so redirecting HOME is enough to make a tmp_path dir qualify.
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


def test_session_end_outside_tracked_work_creates_no_thread(work_env, monkeypatch, tmp_path):
    """A directory that is neither a tracked project nor one of the legacy
    three got no thread before this fix and must not gain one now."""
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


# ===========================================================================
# Project-keyed dedup through the real hook (v0.7.7)
#
# The backend-level contract lives in test_thread_cwd.py. This drives
# session_close.main() — the wiring — because the hook had its own half of the
# bug: the gate matched `cwd.startswith(home + "/aos")`, so every
# ~/aos-releases/<version> directory qualified under a name that changes on
# every update, and none of the ~/project/<name> worktrees qualified at all.
# ===========================================================================

import sqlite3


def _seed_project(work_env, project_id: str, path) -> None:
    conn = sqlite3.connect(str(work_env["db_path"]))
    conn.execute(
        "INSERT OR REPLACE INTO projects (id, title, path) VALUES (?, ?, ?)",
        (project_id, project_id, str(path)),
    )
    conn.commit()
    conn.close()


def _hook(mod, monkeypatch, tmp_path):
    log_dir = tmp_path / ".aos" / "logs"
    monkeypatch.setattr(mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(mod, "LOG_FILE", log_dir / "sessions.jsonl")


def test_ten_session_ends_across_worktrees_and_release_target_make_one_thread(
    work_env, monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))

    project_root = tmp_path / "project" / "aos"
    worktrees = [
        project_root / ".claude" / "worktrees" / slug
        for slug in ("feat-work-10x", "chore-single-node", "feat-telegram")
    ]
    release_target = tmp_path / "aos-releases" / "v0.7.7-abc1234"
    for d in (project_root, *worktrees, release_target):
        d.mkdir(parents=True, exist_ok=True)
    _seed_project(work_env, "aos", project_root)

    mod = _load_session_close_fresh()
    _hook(mod, monkeypatch, tmp_path)

    cwds = [*map(str, worktrees), str(release_target)]
    for i in range(10):
        _run_hook_once(mod, monkeypatch, cwd=cwds[i % len(cwds)],
                       session_id=f"hook-session-{i}")

    eng = work_env["engine"]
    open_threads = [t for t in eng.get_all_threads() if t["status"] != "closed"]
    assert len(open_threads) <= 1, (
        f"10 SessionEnds across three worktrees and the release target produced "
        f"{len(open_threads)} open threads: "
        f"{[(t['id'], t['title'], t['cwd']) for t in open_threads]}"
    )
    assert open_threads[0]["title"] == "Work in aos"
    assert open_threads[0]["project"] == "aos"


def test_release_sibling_directory_no_longer_sneaks_through_the_gate(
    work_env, monkeypatch, tmp_path
):
    """With no `aos` project tracked, ~/aos-releases/<v> must NOT qualify: it is
    a sibling of ~/aos, not a subdirectory of it. The old bare startswith let it
    in, which is the whole origin of the flood."""
    monkeypatch.setenv("HOME", str(tmp_path))
    release_target = tmp_path / "aos-releases" / "v0.7.7-abc1234"
    release_target.mkdir(parents=True)

    mod = _load_session_close_fresh()
    _hook(mod, monkeypatch, tmp_path)
    _run_hook_once(mod, monkeypatch, cwd=str(release_target), session_id="s1")

    eng = work_env["engine"]
    assert [t for t in eng.get_all_threads()] == []
