"""DeploymentHealthCheck (aos#2345): fix() must never self-report success.

Pins: `_fix_qmd_collection` used to append to `self.fixed` whenever the
`qmd` subprocess calls didn't raise — regardless of whether the vault
collection actually came into existence. That produced "Fixed 1 deployment
gap(s)" on every single run forever, because `check()` immediately
re-detected the same gap.

fix() must re-run the detector after acting and derive its verdict from
what actually changed, verified against `qmd collection show <name>`:
  - already-present collection -> OK, never FIXED (nothing to do)
  - missing collection -> add + embed -> re-verify -> FIXED once
  - still missing after acting -> NOTIFY (never a false FIXED)
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import deployment_health
from base import Status
from deployment_health import DeploymentHealthCheck


def _isolate(monkeypatch, tmp_path):
    """Point the check at an empty fake HOME and silence the other three
    sub-checks (service venvs / cron commands / git hooks) so only the QMD
    collection path is under test."""
    monkeypatch.setattr(deployment_health, "HOME", tmp_path)
    monkeypatch.setattr(deployment_health, "AOS", tmp_path / "aos")
    monkeypatch.setattr(deployment_health, "INSTANCE", tmp_path / ".aos")
    monkeypatch.setattr(DeploymentHealthCheck, "_check_service_venvs", lambda self: None)
    monkeypatch.setattr(DeploymentHealthCheck, "_check_cron_commands", lambda self: None)
    monkeypatch.setattr(DeploymentHealthCheck, "_check_git_hooks", lambda self: None)

    qmd_bin = tmp_path / ".bun" / "bin" / "qmd"
    qmd_bin.parent.mkdir(parents=True)
    qmd_bin.touch()
    (tmp_path / "vault").mkdir()
    return qmd_bin


class _FakeQmd:
    """Simulates the real `qmd` CLI: `collection show <name>` is the single
    source of truth for whether the collection exists; `collection add`
    creates it (unless told to refuse, mirroring the real CLI's exit-0
    refusal when the collection already exists)."""

    def __init__(self, present=False):
        self.present = present
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        argv = [str(c) for c in cmd]

        class _Result:
            def __init__(self, returncode, stdout=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""

        if "show" in argv:
            if self.present:
                return _Result(0, "Collection: vault\n  Path: /x/vault\n")
            return _Result(1, "Collection not found: vault\n")
        if "add" in argv:
            self.present = True
            return _Result(0, "Added collection 'vault'.\n")
        if "embed" in argv:
            return _Result(0, "")
        return _Result(0, "")


def test_already_present_collection_is_ok_not_fixed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    fake = _FakeQmd(present=True)
    monkeypatch.setattr(deployment_health.subprocess, "run", fake.run)

    check = DeploymentHealthCheck()
    assert check.check() is True, "an already-present collection must not be reported as an issue"

    # Drive fix() directly anyway (as the runner never would, since check()
    # was True) to pin that it is honest even when invoked on a healthy
    # system: it must never claim to have fixed something that was never
    # broken.
    result = check.fix()
    assert result.status == Status.OK
    assert check.fixed == []


def test_missing_collection_is_fixed_once_then_ok(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    fake = _FakeQmd(present=False)
    monkeypatch.setattr(deployment_health.subprocess, "run", fake.run)

    check = DeploymentHealthCheck()
    assert check.check() is False
    assert any(i["kind"] == "missing_qmd_collection" for i in check.issues)

    result = check.fix()
    assert result.status == Status.FIXED
    assert result.message == "Fixed 1 deployment gap(s)"
    assert len(check.fixed) == 1

    # The remedy must have been verified against `qmd collection show`,
    # not merely assumed because the subprocess call didn't raise.
    assert any("show" in [str(c) for c in call] for call in fake.calls)

    # A fresh cycle must now read the invariant as holding.
    assert check.check() is True
    result2 = check.fix()
    assert result2.status == Status.OK
    assert check.fixed == []


def test_fix_that_does_not_take_never_reports_fixed(monkeypatch, tmp_path):
    """qmd can refuse the add (e.g. 'Collection already exists') on exit 0
    without the collection actually becoming visible via `collection show`
    under the name this check expects — the remedy must be judged on the
    re-check, not on the subprocess exit code."""
    _isolate(monkeypatch, tmp_path)

    class _StubbornQmd(_FakeQmd):
        def run(self, cmd, **kwargs):
            self.calls.append(cmd)
            argv = [str(c) for c in cmd]

            class _Result:
                def __init__(self, returncode, stdout=""):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = ""

            if "show" in argv:
                return _Result(1, "Collection not found: vault\n")
            if "add" in argv:
                # Exits 0 but never actually creates it — the exit-0 refusal
                # the real qmd binary exhibits when a namesake exists.
                return _Result(0, "Collection 'vault' already exists.\n")
            return _Result(0, "")

    fake = _StubbornQmd()
    monkeypatch.setattr(deployment_health.subprocess, "run", fake.run)

    check = DeploymentHealthCheck()
    assert check.check() is False

    result = check.fix()
    assert result.status == Status.NOTIFY
    assert result.status != Status.FIXED
    assert check.fixed == []
