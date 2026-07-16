"""
Tests for ServiceLoadedCheck — the generic "deployed plist but no loaded job"
net (aos#180). Covers the unloaded-service repair, the loaded+healthy pass, the
by-design interval-job non-flap case (scheduler/slack-watch), and the anti-flap
cooldown that stops it re-restarting a service another check just restarted.

All launchd / health / restart I/O is faked; nothing touches real launchd,
~/.aos/, or the network.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

CHECK_PATH = (
    Path(__file__).parent.parent
    / "core" / "infra" / "reconcile" / "checks" / "service_loaded.py"
)

RESIDENT_PLIST = "<plist><dict><key>KeepAlive</key><true/></dict></plist>"
INTERVAL_PLIST = "<plist><dict><key>StartInterval</key><integer>120</integer></dict></plist>"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("service_loaded_under_test", CHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["service_loaded_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    mod = _load_check_module()
    la = tmp_path / "LaunchAgents"
    la.mkdir()
    monkeypatch.setattr(mod.ServiceLoadedCheck, "LA_DIR", la)

    state = {
        "loaded": {},       # label -> bool
        "healthy": {},      # url -> bool
        "restart_age": {},  # label -> float | None
        "restarts": [],     # labels restarted
        "restart_ok": True,
    }
    monkeypatch.setattr(mod, "is_loaded", lambda label: state["loaded"].get(label, True))
    monkeypatch.setattr(mod, "last_restart_age", lambda label: state["restart_age"].get(label))

    def fake_restart(label, plist, actor="?"):
        state["restarts"].append(label)
        return state["restart_ok"]
    monkeypatch.setattr(mod, "restart_launchagent", fake_restart)
    monkeypatch.setattr(
        mod.ServiceLoadedCheck, "_is_healthy",
        lambda self, url: state["healthy"].get(url, True),
    )

    def write(label, content):
        (la / f"{label}.plist").write_text(content)

    yield {"mod": mod, "la": la, "state": state, "write": write}
    sys.modules.pop("service_loaded_under_test", None)


def _check(env):
    return env["mod"].ServiceLoadedCheck()


def test_all_loaded_and_healthy_passes(env):
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["write"]("com.aos.transcriber", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": True, "com.aos.transcriber": True}

    c = _check(env)
    assert c.check() is True
    assert env["state"]["restarts"] == []


def test_unloaded_service_is_restarted(env):
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": False}

    c = _check(env)
    assert c.check() is False
    result = c.fix()
    assert result.status == env["mod"].Status.FIXED
    assert env["state"]["restarts"] == ["com.aos.bridge"]


def test_interval_job_loaded_is_not_flagged_or_flapped(env):
    """scheduler/slack-watch: StartInterval + no KeepAlive. Loaded-but-idle is
    NORMAL — the check must pass and never restart them."""
    env["write"]("com.aos.scheduler", INTERVAL_PLIST)
    env["write"]("com.aos.slack-watch", INTERVAL_PLIST)
    env["state"]["loaded"] = {"com.aos.scheduler": True, "com.aos.slack-watch": True}
    # Even if some health probe would fail, interval jobs are never probed.
    env["state"]["healthy"] = {}

    c = _check(env)
    assert c.check() is True
    c.fix()  # should be a no-op OK path
    assert env["state"]["restarts"] == []


def test_unregistered_interval_job_is_restarted(env):
    """An interval job that isn't even loaded IS broken — it should be
    registered. (Distinct from the loaded-but-idle non-flap case.)"""
    env["write"]("com.aos.scheduler", INTERVAL_PLIST)
    env["state"]["loaded"] = {"com.aos.scheduler": False}

    c = _check(env)
    assert c.check() is False
    c.fix()
    assert env["state"]["restarts"] == ["com.aos.scheduler"]


def test_loaded_but_unhealthy_outside_cooldown_is_restarted(env):
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": True}
    env["state"]["healthy"] = {"http://127.0.0.1:4098/health": False}
    env["state"]["restart_age"] = {"com.aos.bridge": None}  # no recent restart

    c = _check(env)
    assert c.check() is False
    result = c.fix()
    assert result.status == env["mod"].Status.FIXED
    assert env["state"]["restarts"] == ["com.aos.bridge"]


def test_loaded_but_unhealthy_within_cooldown_is_skipped(env):
    """Anti-flap: don't re-restart a service another check restarted seconds
    ago. Health-triggered restarts respect the cooldown."""
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": True}
    env["state"]["healthy"] = {"http://127.0.0.1:4098/health": False}
    env["state"]["restart_age"] = {"com.aos.bridge": 20.0}  # restarted 20s ago

    c = _check(env)
    assert c.check() is False
    result = c.fix()
    assert env["state"]["restarts"] == []  # skipped
    assert result.status == env["mod"].Status.NOTIFY


def test_unloaded_ignores_cooldown(env):
    """An UNLOADED service is always restarted — cooldown only guards
    health-triggered restarts, not the critical silent-death state."""
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": False}
    env["state"]["restart_age"] = {"com.aos.bridge": 5.0}  # very recent

    c = _check(env)
    c.fix()
    assert env["state"]["restarts"] == ["com.aos.bridge"]


def test_failed_reload_notifies(env):
    env["write"]("com.aos.bridge", RESIDENT_PLIST)
    env["state"]["loaded"] = {"com.aos.bridge": False}
    env["state"]["restart_ok"] = False

    c = _check(env)
    result = c.fix()
    assert result.status == env["mod"].Status.NOTIFY
    assert result.notify is True


def test_periodic_fix_opt_in_is_set(env):
    assert env["mod"].ServiceLoadedCheck.periodic_fix is True
