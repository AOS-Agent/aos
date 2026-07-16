"""
Unit tests for the shared guarded service choke-point
(core/infra/lib/service_ctl.py).

restart_launchagent is the single path every AOS service restart routes
through. Its contract:

  - bootout → settle (wait for launchd to release) → bootstrap → VERIFY the job
    registered → retry the bootstrap → kickstart
  - NEVER return having silently left the job unloaded: True only when the job
    is verified loaded; False (with a `failed`/`error` audit line) otherwise
  - a drain-blocking kickstart TimeoutExpired is non-fatal (job already loaded)
  - every action appended to ~/.aos/logs/service-lifecycle.jsonl

These lock the settle/verify/retry logic that used to live inline in
check-update (the v0.6.10 bridge-vanish, aos#180). All launchctl I/O is faked;
nothing here touches real launchd or ~/.aos/.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB_PATH = Path(__file__).parent.parent / "core" / "infra" / "lib" / "service_ctl.py"


def _load_service_ctl():
    spec = importlib.util.spec_from_file_location("service_ctl_under_test", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["service_ctl_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeLaunchctl:
    """Fakes `subprocess.run` for launchctl. `print` returns rc 0/1 from a
    scripted sequence (0 = job loaded); every other subcommand succeeds.
    Optionally raises TimeoutExpired on kickstart to model a draining -k."""

    def __init__(self, print_loaded_sequence, kickstart_timeout=False):
        self.print_seq = list(print_loaded_sequence)
        self.kickstart_timeout = kickstart_timeout
        self.calls = []

    def run(self, cmd, capture_output=True, timeout=None, text=False):
        self.calls.append(list(cmd))
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "print":
            loaded = self.print_seq.pop(0) if self.print_seq else False
            return subprocess.CompletedProcess(cmd, 0 if loaded else 1)
        if sub == "kickstart" and self.kickstart_timeout:
            raise subprocess.TimeoutExpired(cmd, timeout or 10)
        return subprocess.CompletedProcess(cmd, 0)

    def subcommands(self):
        return [c[1] for c in self.calls if len(c) > 1]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """service_ctl module with launchctl + audit log + sleep isolated."""
    mod = _load_service_ctl()
    log = tmp_path / "service-lifecycle.jsonl"
    monkeypatch.setattr(mod, "LIFECYCLE_LOG", log)
    monkeypatch.setattr(mod, "LA_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    plist = tmp_path / "com.aos.bridge.plist"
    plist.write_text("<plist/>")
    yield {"mod": mod, "log": log, "plist": plist}
    sys.modules.pop("service_ctl_under_test", None)


def _audit_lines(log_path):
    if not log_path.exists():
        return []
    return [json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()]


def test_happy_path_settles_verifies_and_returns_true(svc, monkeypatch):
    mod, log, plist = svc["mod"], svc["log"], svc["plist"]
    # settle: first print says "released" (not loaded); verify: print says loaded.
    fake = FakeLaunchctl(print_loaded_sequence=[False, True])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    assert mod.restart_launchagent("com.aos.bridge", plist, actor="test") is True

    subs = fake.subcommands()
    # bootout precedes bootstrap precedes kickstart.
    assert subs.index("bootout") < subs.index("bootstrap") < subs.index("kickstart")
    # A settle probe (print) sits between bootout and bootstrap.
    assert subs.index("bootout") < subs.index("print") < subs.index("bootstrap")

    entries = _audit_lines(log)
    assert entries[-1]["result"] == "ok"
    assert entries[-1]["service"] == "com.aos.bridge"
    assert entries[-1]["actor"] == "test"


def test_bootstrap_retries_until_job_registers(svc, monkeypatch):
    mod, plist = svc["mod"], svc["plist"]
    # settle released; first verify fails (lost race), second verify loaded.
    fake = FakeLaunchctl(print_loaded_sequence=[False, False, True])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    assert mod.restart_launchagent("com.aos.bridge", plist, actor="test") is True
    # bootstrap attempted at least twice (the retry).
    assert fake.subcommands().count("bootstrap") >= 2


def test_never_leaves_silently_unloaded_returns_false(svc, monkeypatch):
    mod, log, plist = svc["mod"], svc["log"], svc["plist"]
    # settle released; every verify fails — job never registers.
    fake = FakeLaunchctl(print_loaded_sequence=[False, False, False, False, False])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    assert mod.restart_launchagent("com.aos.bridge", plist, actor="test") is False
    # kickstart must NOT run when the job never loaded (don't kick a dead job).
    assert "kickstart" not in fake.subcommands()
    assert _audit_lines(log)[-1]["result"] == "failed"


def test_missing_plist_fails_without_touching_launchctl(svc, monkeypatch):
    mod, log = svc["mod"], svc["log"]
    fake = FakeLaunchctl(print_loaded_sequence=[])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    missing = svc["plist"].parent / "com.aos.ghost.plist"
    assert mod.restart_launchagent("com.aos.ghost", missing, actor="test") is False
    assert fake.calls == []  # never shelled out
    assert _audit_lines(log)[-1]["result"] == "error"


def test_kickstart_timeout_is_non_fatal(svc, monkeypatch):
    mod, log, plist = svc["mod"], svc["log"], svc["plist"]
    fake = FakeLaunchctl(print_loaded_sequence=[False, True], kickstart_timeout=True)
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    # Job verified loaded before the kickstart; a draining -k must not fail it.
    assert mod.restart_launchagent("com.aos.bridge", plist, actor="test") is True
    assert _audit_lines(log)[-1]["result"] == "ok"


def test_last_restart_age_reads_the_log(svc, monkeypatch):
    mod, plist = svc["mod"], svc["plist"]
    fake = FakeLaunchctl(print_loaded_sequence=[False, True])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))

    assert mod.last_restart_age("com.aos.bridge") is None  # nothing logged yet
    mod.restart_launchagent("com.aos.bridge", plist, actor="test")
    age = mod.last_restart_age("com.aos.bridge")
    assert age is not None and age < 60


def test_audit_logging_failure_never_breaks_restart(svc, monkeypatch):
    """A broken audit log must not sink a restart — logging is best-effort."""
    mod, plist = svc["mod"], svc["plist"]
    fake = FakeLaunchctl(print_loaded_sequence=[False, True])
    monkeypatch.setattr(mod, "subprocess", _sp_with(fake))
    # Point the log at a path that cannot be created (parent is a file).
    bad = svc["log"].parent / "afile"
    bad.write_text("x")
    monkeypatch.setattr(mod, "LIFECYCLE_LOG", bad / "nested" / "log.jsonl")

    assert mod.restart_launchagent("com.aos.bridge", plist, actor="test") is True


class _sp_with:
    """A stand-in `subprocess` module exposing only .run (delegated to the fake)
    and the real TimeoutExpired/CompletedProcess symbols the code references."""

    def __init__(self, fake):
        self.run = fake.run
        self.TimeoutExpired = subprocess.TimeoutExpired
        self.CompletedProcess = subprocess.CompletedProcess
