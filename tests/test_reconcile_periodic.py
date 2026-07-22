"""
Tests for the periodic reconcile mode (the 30-min between-deploy cron).

Contract:
  - Full fix-mode stays deploy-only. On a periodic run, a check that does NOT
    opt in with periodic_fix=True is report-only — its fix() is never called.
  - A check that sets periodic_fix=True (ServiceLoadedCheck) MAY repair on the
    periodic run — its fix() IS called.
  - Only periodic-fix results (and check crashes) notify; the standing
    conditions the deploy reconcile owns are logged but not re-pinged.

Fake checks are injected via _load_checks; all I/O (Telegram, state files, dedup
set) is redirected to tmp so nothing touches ~/.aos/ or the network.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).parent.parent / "core" / "infra" / "reconcile" / "runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("reconcile_runner_under_test", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_runner_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runner(tmp_path, monkeypatch):
    mod = _load_runner()
    monkeypatch.setattr(mod, "LOG_FILE", tmp_path / "reconcile.jsonl")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "reconcile-state.json")
    monkeypatch.setenv("HOME", str(tmp_path))  # isolates the dedup seen file
    sent = []
    monkeypatch.setattr(mod, "_notify_telegram", lambda msg: sent.append(msg))
    yield {"mod": mod, "sent": sent, "tmp": tmp_path}
    sys.modules.pop("reconcile_runner_under_test", None)


def _make_checks(mod):
    CheckResult, ReconcileCheck, Status = mod.CheckResult, mod.ReconcileCheck, mod.Status
    calls = {"deploy_only": 0, "periodic_svc": 0}

    class Healthy(ReconcileCheck):
        name = "healthy"
        def check(self):  # noqa: D401
            return True

    class DeployOnlyBroken(ReconcileCheck):
        name = "deploy_only"
        description = "deploy-only thing"
        periodic_fix = False
        def check(self):
            return False
        def fix(self):
            calls["deploy_only"] += 1
            return CheckResult(self.name, Status.FIXED, "deploy fixed")

    class PeriodicBroken(ReconcileCheck):
        name = "periodic_svc"
        description = "service loaded"
        periodic_fix = True
        def check(self):
            return False
        def fix(self):
            calls["periodic_svc"] += 1
            return CheckResult(self.name, Status.FIXED, "restarted dead service")

    return [Healthy, DeployOnlyBroken, PeriodicBroken], calls


def test_periodic_only_fixes_periodic_fix_checks(runner, monkeypatch):
    mod = runner["mod"]
    checks, calls = _make_checks(mod)
    monkeypatch.setattr(mod, "_load_checks", lambda: (checks, []))

    results = mod.run_all(periodic=True)

    assert calls["periodic_svc"] == 1, "periodic_fix check must be repaired"
    assert calls["deploy_only"] == 0, "deploy-only check must NOT be fixed on periodic run"

    by_name = {r.name: r for r in results}
    assert by_name["periodic_svc"].status == mod.Status.FIXED
    assert by_name["deploy_only"].status == mod.Status.NOTIFY  # report-only "would fix"
    assert by_name["healthy"].status == mod.Status.OK


def test_periodic_notifies_only_for_periodic_fix_results(runner, monkeypatch):
    mod = runner["mod"]
    checks, _ = _make_checks(mod)
    monkeypatch.setattr(mod, "_load_checks", lambda: (checks, []))

    mod.run_all(periodic=True)

    joined = "\n".join(runner["sent"])
    # A repaired service must ping the operator — but in human copy, not the
    # raw check slug (aos#170). The alert carries the humanized message, not
    # "periodic_svc".
    assert runner["sent"], "a repaired service must ping the operator"
    assert "dead service" in joined.lower(), "the ping must carry the humanized message"
    assert "periodic_svc" not in joined, "the raw check slug must never reach the phone"
    # Standing deploy-owned conditions must not nag on periodic runs, in any form.
    assert "deploy_only" not in joined
    assert "deploy fixed" not in joined.lower()


def test_deploy_mode_fixes_everything(runner, monkeypatch):
    mod = runner["mod"]
    checks, calls = _make_checks(mod)
    monkeypatch.setattr(mod, "_load_checks", lambda: (checks, []))

    mod.run_all(dry_run=False)

    assert calls["deploy_only"] == 1, "deploy run must fix deploy-only checks"
    assert calls["periodic_svc"] == 1


def test_periodic_uses_separate_dedup_file(runner, monkeypatch):
    mod = runner["mod"]
    checks, _ = _make_checks(mod)
    monkeypatch.setattr(mod, "_load_checks", lambda: (checks, []))

    mod.run_all(periodic=True)
    periodic_seen = runner["tmp"] / ".aos" / "state" / "reconcile-notified-periodic.json"
    deploy_seen = runner["tmp"] / ".aos" / "state" / "reconcile-notified.json"
    assert periodic_seen.exists()
    assert not deploy_seen.exists()  # deploy set untouched by the periodic run
