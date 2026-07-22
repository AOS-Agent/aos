"""
Tests for reconcile's check-loading resilience.

Regression context: checks/__init__.py imports every check module at module
scope and exposes ALL_CHECKS. A single bad import there raised before any
logging happened, which silently disabled all 22 checks on a live machine for
roughly three months — the state file just stopped updating, so it read as
dormant rather than dead.

Contract:
  - Healthy registry: _load_checks returns ALL_CHECKS and no failures.
  - Broken registry: _load_checks falls back to per-module loading, returns the
    checks that DID import, and reports the breakage as ERROR results.
  - A single unimportable module costs exactly one check, not all of them.
  - Load failures reach run_all's results so they are logged and notified.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).parent.parent / "core" / "infra" / "reconcile" / "runner.py"

GOOD_CHECK = '''
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


class {cls}(ReconcileCheck):
    name = "{name}"
    description = "fixture check"

    def check(self) -> bool:
        return True

    def fix(self):
        return CheckResult(self.name, Status.OK, "ok")
'''

BROKEN_CHECK = '''
from ..base import CheckResult, ReconcileCheck, Status  # noqa: F401

class NeverLoads(ReconcileCheck):
    name = "never_loads"
'''


def _load_runner():
    spec = importlib.util.spec_from_file_location("reconcile_runner_loader_test", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_runner_loader_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runner():
    mod = _load_runner()
    yield mod
    sys.modules.pop("reconcile_runner_loader_test", None)
    for key in [k for k in sys.modules if k.startswith("_reconcile_check_")]:
        sys.modules.pop(key, None)


@pytest.fixture
def checks_dir(tmp_path):
    d = tmp_path / "checks"
    d.mkdir()
    (d / "__init__.py").write_text("raise ImportError('registry is broken')\n")
    (d / "alpha_check.py").write_text(GOOD_CHECK.format(cls="AlphaCheck", name="alpha"))
    (d / "beta_check.py").write_text(GOOD_CHECK.format(cls="BetaCheck", name="beta"))
    (d / "broken_check.py").write_text(BROKEN_CHECK)
    return d


def test_healthy_registry_returns_all_checks_and_no_failures(runner):
    """The fast path is unchanged: real ALL_CHECKS, zero reported failures."""
    classes, failures = runner._load_checks()
    assert failures == []
    assert len(classes) > 0
    assert all(issubclass(c, runner.ReconcileCheck) for c in classes)


def test_one_bad_module_costs_one_check_not_all(runner, checks_dir):
    """The whole point: a broken module must not take the good ones with it."""
    classes, failures = runner._load_checks_individually(checks_dir)

    names = sorted(c.name for c in classes)
    assert names == ["alpha", "beta"], "good checks must still load"

    assert len(failures) == 1
    assert failures[0].status is runner.Status.ERROR
    assert failures[0].name == "load:broken_check"
    assert failures[0].notify is True
    assert "relative import" in failures[0].detail


def test_broken_module_is_reported_not_swallowed(runner, checks_dir):
    """The silent-failure bug: breakage must produce a visible, notifying result."""
    _, failures = runner._load_checks_individually(checks_dir)
    assert failures, "an unimportable check must never fail silently"
    assert all(f.notify for f in failures)
    assert all(f.detail for f in failures), "traceback must be preserved for triage"


def test_broken_registry_falls_back_and_reports(runner, checks_dir, monkeypatch):
    """When checks/__init__.py raises, degrade to per-module loading."""
    import types

    fixture_result = runner._load_checks_individually(checks_dir)
    # A `checks` module with no ALL_CHECKS makes `from checks import ALL_CHECKS`
    # raise ImportError — the same shape as a bad check module poisoning it.
    monkeypatch.setitem(sys.modules, "checks", types.ModuleType("checks"))
    monkeypatch.setattr(runner, "_load_checks_individually", lambda *a, **k: fixture_result)

    classes, failures = runner._load_checks()

    assert sorted(c.name for c in classes) == ["alpha", "beta"], "must still return working checks"
    assert failures[0].name == "reconcile_check_registry"
    assert failures[0].status is runner.Status.ERROR
    assert failures[0].notify is True
    assert any(f.name == "load:broken_check" for f in failures)


def test_load_failures_reach_run_all_results(runner, checks_dir, tmp_path, monkeypatch):
    """Load failures must be logged and notified, not dropped on the floor."""
    monkeypatch.setattr(runner, "LOG_FILE", tmp_path / "reconcile.jsonl")
    monkeypatch.setattr(runner, "STATE_FILE", tmp_path / "reconcile-state.json")
    monkeypatch.setenv("HOME", str(tmp_path))

    classes, failures = runner._load_checks_individually(checks_dir)
    monkeypatch.setattr(runner, "_load_checks", lambda: (classes, failures))

    results = runner.run_all(dry_run=True)

    errored = [r for r in results if r.status is runner.Status.ERROR]
    assert any(r.name == "load:broken_check" for r in errored)
    # and the healthy checks still ran
    assert {"alpha", "beta"} <= {r.name for r in results}
