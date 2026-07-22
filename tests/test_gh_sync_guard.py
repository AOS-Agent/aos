"""
GitHub Issues sync must never fire from the test suite.

Context: the work engine shells out to `gh issue create` / `gh issue close`
for every project="aos" task. Test fixtures (conftest.populated_work_env,
tests in test_engine.py) create exactly such tasks, and before the guard
existed the suite filed thousands of real issues on the public repo.

The guard lives in three places (the gh-sync code is duplicated):
  - core/engine/work/backend.py   (live engine, used by cli.py)
  - core/engine/work/engine.py    (legacy engine, same API)
  - core/qareen/ontology/listeners.py (event-bus listener)

Contract proven here:
  1. Sync is hard-disabled under pytest, even with AOS_GITHUB_SYNC=1 set.
  2. Outside pytest, sync is opt-in: off by default, on only when
     AOS_GITHUB_SYNC is 1/true/yes.
  3. The exact fixture path that used to leak (add_task(project="aos") →
     complete_task) makes no `gh` subprocess call at all.
"""

import subprocess

import backend  # noqa: F401 — also puts the repo root on sys.path
import engine
import pytest

from core.qareen.ontology import listeners

# (module, create_fn_name, close_fn_name)
GH_MODULES = [
    (backend, "_gh_create_issue", "_gh_close_issue"),
    (engine, "_gh_create_issue", "_gh_close_issue"),
    (listeners, "_gh_create_issue_sync", "_gh_close_issue_sync"),
]
IDS = [m.__name__ for m, _, _ in GH_MODULES]


class GhReached(AssertionError):
    """Raised when a guarded code path still reaches subprocess.run."""


@pytest.fixture()
def forbid_subprocess(monkeypatch):
    """Make ANY subprocess.run call explode — proves the guard short-circuits
    before the network layer, not that the gh call merely fails."""

    def boom(*args, **kwargs):
        raise GhReached(f"subprocess.run was reached: args={args!r}")

    monkeypatch.setattr(subprocess, "run", boom)


# ---------------------------------------------------------------------------
# 1. Hard-disabled under pytest, even when explicitly opted in
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,create_fn,close_fn", GH_MODULES, ids=IDS)
def test_sync_blocked_under_pytest_even_with_optin(
    mod, create_fn, close_fn, monkeypatch, forbid_subprocess
):
    monkeypatch.setenv("AOS_GITHUB_SYNC", "1")

    assert mod._in_pytest() is True
    assert mod._gh_sync_enabled() is False

    # Both entry points return their failure value WITHOUT touching
    # subprocess (forbid_subprocess would raise GhReached otherwise).
    assert getattr(mod, create_fn)("t#9999", "guard probe") is None
    assert getattr(mod, close_fn)("t#9999") is False


# ---------------------------------------------------------------------------
# 2. Outside pytest: opt-in only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,create_fn,close_fn", GH_MODULES, ids=IDS)
def test_sync_is_opt_in_outside_pytest(mod, create_fn, close_fn, monkeypatch):
    # Simulate a non-pytest process
    monkeypatch.setattr(mod, "_in_pytest", lambda: False)

    monkeypatch.delenv("AOS_GITHUB_SYNC", raising=False)
    assert mod._gh_sync_enabled() is False, "sync must be OFF by default"

    for off_value in ("0", "false", "no", ""):
        monkeypatch.setenv("AOS_GITHUB_SYNC", off_value)
        assert mod._gh_sync_enabled() is False

    for on_value in ("1", "true", "yes", "TRUE"):
        monkeypatch.setenv("AOS_GITHUB_SYNC", on_value)
        assert mod._gh_sync_enabled() is True


# ---------------------------------------------------------------------------
# 3. The fixture path that used to leak: full aos-task lifecycle, no network
# ---------------------------------------------------------------------------

def test_aos_task_lifecycle_makes_no_gh_calls(
    populated_work_env, monkeypatch, forbid_subprocess
):
    """add_task(project='aos') + complete_task were the exact calls that
    filed real issues from conftest fixtures. Run them with subprocess
    booby-trapped and the env opt-in set: nothing may reach subprocess."""
    monkeypatch.setenv("AOS_GITHUB_SYNC", "1")
    eng = populated_work_env["engine"]

    task = eng.add_task("Guard probe task", project="aos", priority=2)
    assert task is not None

    completed = eng.complete_task(task["id"])
    assert completed is not None


# ---------------------------------------------------------------------------
# 4. The conftest tripwire itself works
# ---------------------------------------------------------------------------

def test_conftest_tripwire_blocks_gh_invocations():
    """The autouse no_gh_subprocess fixture must reject any `gh` command."""
    with pytest.raises(RuntimeError, match="Blocked `gh` invocation"):
        subprocess.run(["gh", "issue", "list"], capture_output=True)

    with pytest.raises(RuntimeError, match="Blocked `gh` invocation"):
        subprocess.run(["/opt/homebrew/bin/gh", "auth", "status"])

    # Non-gh commands still work normally.
    result = subprocess.run(["true"], capture_output=True)
    assert result.returncode == 0
