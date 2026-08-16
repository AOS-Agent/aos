"""
Tests for the operator service opt-out (aos: "off" must be a valid state).

Contract:
  - ~/.aos/config/services.yaml declares which services the operator switched
    off on THIS machine.
  - A reconcile check that owns a disabled service is skipped entirely — its
    check() and fix() are never called — and reports DISABLED, not a failure.
  - restart_launchagent() refuses to start a disabled service no matter who
    asks, so no check, migration, or watchdog can override the operator.
  - Malformed config fails OPEN (services run) but says so loudly, so the
    opt-out can never be silently ignored.

Everything is redirected to tmp; nothing reads or writes the real ~/.aos/.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
REGISTRY_PATH = REPO / "core" / "infra" / "lib" / "service_registry.py"
RUNNER_PATH = REPO / "core" / "infra" / "reconcile" / "runner.py"
CTL_PATH = REPO / "core" / "infra" / "lib" / "service_ctl.py"


def _load(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


# ── The config reader ────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path, monkeypatch):
    mod = _load(REGISTRY_PATH, "registry_optout_under_test")
    monkeypatch.setattr(mod, "OPERATOR_CONFIG", tmp_path / "services.yaml")
    yield mod
    sys.modules.pop("registry_optout_under_test", None)


def test_missing_config_disables_nothing(registry):
    assert registry.disabled_services() == frozenset()
    assert registry.services_config_error() is None
    assert registry.is_disabled("transcriber") is False


def test_declared_services_are_disabled(registry):
    registry.OPERATOR_CONFIG.write_text("disabled:\n  - transcriber\n  - mesh\n")
    assert registry.disabled_services() == frozenset({"transcriber", "mesh"})
    assert registry.is_disabled("transcriber") is True
    assert registry.is_disabled("bridge") is False
    assert registry.services_config_error() is None


def test_empty_list_is_valid_and_quiet(registry):
    registry.OPERATOR_CONFIG.write_text("disabled: []\n")
    assert registry.disabled_services() == frozenset()
    assert registry.services_config_error() is None


def test_malformed_yaml_fails_open_but_reports(registry):
    """A syntax error must not shut down the operator's services — but it also
    must not silently swallow their opt-out."""
    registry.OPERATOR_CONFIG.write_text("disabled: [unclosed\n")
    assert registry.disabled_services() == frozenset()
    err = registry.services_config_error()
    assert err and "could not parse" in err


def test_wrong_shape_reports_rather_than_guessing(registry):
    registry.OPERATOR_CONFIG.write_text("disabled: transcriber\n")
    assert registry.disabled_services() == frozenset()
    err = registry.services_config_error()
    assert err and "must be a list" in err


# ── The runner ───────────────────────────────────────────────────────────────

@pytest.fixture
def runner(tmp_path, monkeypatch):
    mod = _load(RUNNER_PATH, "runner_optout_under_test")
    monkeypatch.setattr(mod, "LOG_FILE", tmp_path / "reconcile.jsonl")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "reconcile-state.json")
    monkeypatch.setenv("HOME", str(tmp_path))
    # ReconcileCheck.precondition() defaults to aos_installed(), which requires
    # both trees to exist. Without them every check SKIPs before its own logic
    # runs, and these tests would pass while asserting nothing about the
    # opt-out. Create them so the checks under test actually execute.
    (tmp_path / "aos").mkdir(exist_ok=True)
    (tmp_path / ".aos").mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "_notify_telegram", lambda msg: None)
    yield mod
    sys.modules.pop("runner_optout_under_test", None)


def _owned_check(mod, calls):
    class OwnsDisabledService(mod.ReconcileCheck):
        name = "transcriber_service"
        description = "transcriber is running"
        service = "transcriber"

        def check(self):
            calls.append("check")
            return False

        def fix(self):
            calls.append("fix")
            return mod.CheckResult(self.name, mod.Status.FIXED, "restarted")

    return OwnsDisabledService


def test_disabled_service_check_is_never_run(runner, monkeypatch):
    """The bug this closes: a disabled service reported as unhealthy, then
    restarted — reconcile overriding a deliberate operator decision."""
    calls: list[str] = []
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset({"transcriber"}))
    monkeypatch.setattr(runner, "services_config_error", lambda: None)
    monkeypatch.setattr(
        runner, "_load_checks", lambda: ([_owned_check(runner, calls)], [])
    )

    results = runner.run_all(dry_run=False)

    assert calls == [], "check()/fix() ran for a service the operator disabled"
    assert len(results) == 1
    assert results[0].status is runner.Status.DISABLED
    assert "disabled by the operator" in results[0].message


def test_enabled_service_check_still_runs(runner, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset())
    monkeypatch.setattr(runner, "services_config_error", lambda: None)
    monkeypatch.setattr(
        runner, "_load_checks", lambda: ([_owned_check(runner, calls)], [])
    )

    results = runner.run_all(dry_run=False)

    assert calls == ["check", "fix"]
    assert results[0].status is runner.Status.FIXED


def test_check_without_a_service_is_unaffected(runner, monkeypatch):
    """Only checks that declare `service` participate — everything else runs
    regardless of what is disabled."""
    calls: list[str] = []
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset({"transcriber"}))
    monkeypatch.setattr(runner, "services_config_error", lambda: None)

    class Unowned(runner.ReconcileCheck):
        name = "claude_md"

        def check(self):
            calls.append("check")
            return True

    monkeypatch.setattr(runner, "_load_checks", lambda: ([Unowned], []))
    results = runner.run_all(dry_run=False)

    assert calls == ["check"]
    assert results[0].status is runner.Status.OK


def test_broken_config_is_surfaced_not_swallowed(runner, monkeypatch):
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset())
    monkeypatch.setattr(
        runner, "services_config_error", lambda: "services.yaml: could not parse YAML: x"
    )
    monkeypatch.setattr(runner, "_load_checks", lambda: ([], []))

    results = runner.run_all(dry_run=False)

    assert any(
        r.name == "services_config"
        and r.status is runner.Status.NOTIFY
        and r.notify
        for r in results
    ), "a broken opt-out config must notify — fail-open silently ignores it"


def test_disabled_count_reaches_state_file(runner, monkeypatch):
    """Qareen reads this state file; DISABLED must be countable, not lumped in
    with failures."""
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset({"transcriber"}))
    monkeypatch.setattr(runner, "services_config_error", lambda: None)
    monkeypatch.setattr(
        runner, "_load_checks", lambda: ([_owned_check(runner, [])], [])
    )

    runner.run_all(dry_run=False)

    import json
    state = json.loads(runner.STATE_FILE.read_text())
    assert state["disabled"] == 1
    assert state["error"] == 0
    assert state["notify"] == 0
    assert state["checks"]["transcriber_service"]["status"] == "disabled"


# ── The restart choke-point ──────────────────────────────────────────────────

def test_restart_refuses_a_disabled_service(tmp_path, monkeypatch):
    """Every restart in AOS routes through here, so refusing at this one point
    means nothing can switch a disabled service back on."""
    ctl = _load(CTL_PATH, "service_ctl_under_test")
    monkeypatch.setattr(ctl, "LIFECYCLE_LOG", tmp_path / "lifecycle.jsonl")
    monkeypatch.setattr(ctl, "_operator_disabled", lambda label: True)

    ran: list[list[str]] = []
    monkeypatch.setattr(ctl.subprocess, "run", lambda cmd, **kw: ran.append(cmd))

    # True, not False: the job is in the state the operator asked for, so
    # callers must not escalate this as a failed restart.
    assert ctl.restart_launchagent("com.aos.transcriber", actor="test") is True
    assert ran == [], "launchctl was invoked for a disabled service"

    audit = (tmp_path / "lifecycle.jsonl").read_text()
    assert "skipped" in audit and "disabled by operator" in audit

    sys.modules.pop("service_ctl_under_test", None)


# ── The import that must not silently stub ───────────────────────────────────

def test_runner_binds_the_real_registry_under_its_own_sys_path():
    """The runner falls back to a no-op `disabled_services()` if the registry
    can't be imported, so a path problem would not crash — it would silently
    disable every opt-out and start services the operator turned off.

    That failure mode is not hypothetical here: a relative import that resolved
    under pytest but not under the runner's own sys.path layout silently
    disabled all 22 checks on a live machine for ~3.5 months.

    So this must run the runner the way the runner actually runs — as a script,
    in its own subprocess — not imported under pytest's sys.path, which is the
    exact discrepancy that hid the original bug.
    """
    import subprocess
    import textwrap

    probe = textwrap.dedent("""
        import importlib.util, sys
        from pathlib import Path
        p = Path(sys.argv[1])
        spec = importlib.util.spec_from_file_location("runner_probe", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["runner_probe"] = m
        spec.loader.exec_module(m)
        print(getattr(m.disabled_services, "__module__", "MISSING"))
    """)
    out = subprocess.run(
        [sys.executable, "-c", probe, str(RUNNER_PATH)],
        capture_output=True, text=True, timeout=60, cwd=REPO,
    )
    assert out.returncode == 0, f"runner failed to import standalone: {out.stderr}"
    bound = out.stdout.strip()
    assert bound.endswith("service_registry"), (
        f"runner bound `{bound}` for disabled_services, not the real registry — "
        "the fallback stub is active and every operator opt-out is being ignored"
    )


def test_optout_outranks_precondition(runner, monkeypatch, tmp_path):
    """A disabled service reports DISABLED even when its prerequisites are gone.

    precondition() was added after this opt-out was written, and it runs on
    every check. If it were evaluated first, a disabled service on a machine
    missing its prereqs would report SKIP — "could not verify" — which is a
    statement about the machine, not about the operator's decision. The
    operator's intent is knowable without evaluating anything, so it is
    answered first.
    """
    # Remove the install so precondition() would fail if it were reached.
    for d in ("aos", ".aos"):
        (tmp_path / d).rmdir()

    calls: list[str] = []
    monkeypatch.setattr(runner, "disabled_services", lambda: frozenset({"transcriber"}))
    monkeypatch.setattr(runner, "services_config_error", lambda: None)
    monkeypatch.setattr(
        runner, "_load_checks", lambda: ([_owned_check(runner, calls)], [])
    )

    results = runner.run_all(dry_run=False)

    assert results[0].status is runner.Status.DISABLED, (
        f"reported {results[0].status} — precondition() was evaluated before "
        "the operator's opt-out"
    )
    assert calls == []
