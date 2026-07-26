"""NetworkBindingCheck: no AOS-managed service listens on a wildcard address.

Pins the behaviour that made the Qareen 4096 leak possible and would let it
recur: a wildcard listener owned by a *child* of the launchd job must still be
reported (uvicorn's reload worker holds the socket), non-AOS jobs must NOT be
reported (flagging the operator's caddy sites trains them to ignore the check),
a declared 0.0.0.0 in a stopped service's plist still counts, and fix() never
repairs — it notifies.
"""
import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import network_binding
from base import Status
from network_binding import NetworkBindingCheck

LOOPBACK_PLIST = {
    "Label": "com.aos.bridge",
    "ProgramArguments": ["/python", "-m", "bridge", "--host", "127.0.0.1"],
}
WILDCARD_PLIST = {
    "Label": "com.aos.qareen",
    "ProgramArguments": ["/python", "-m", "uvicorn", "--host", "0.0.0.0", "--port", "4096"],
    "EnvironmentVariables": {"AOS_QAREEN_HOST": "0.0.0.0"},
}
# Not com.aos.*, and nothing pointing into the AOS tree -> out of scope.
FOREIGN_PLIST = {
    "Label": "com.hish.caddy",
    "ProgramArguments": ["/opt/homebrew/bin/caddy", "run", "--bind", "0.0.0.0"],
}
# Different label namespace, but runs out of an AOS venv -> in scope.
DEV_PLIST = {
    "Label": "com.agent.qareen-dev",
    "ProgramArguments": [
        "/Users/x/.aos/services/qareen/.venv/bin/python", "-c",
        "uvicorn.run('qareen.main:app', host='0.0.0.0', port=4097)",
    ],
}


def _agents(monkeypatch, tmp_path, plists):
    d = tmp_path / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    for data in plists:
        with open(d / f"{data['Label']}.plist", "wb") as fh:
            plistlib.dump(data, fh)
    monkeypatch.setattr(network_binding, "LAUNCH_AGENTS", d)
    monkeypatch.setattr(network_binding, "ALLOW_CONFIG", tmp_path / "network.yaml")
    return d


def _stub_runtime(monkeypatch, pids=None, listeners=None, kids=None):
    pids = pids or {}
    monkeypatch.setattr(network_binding, "_job_pid", lambda label: pids.get(label))
    monkeypatch.setattr(network_binding, "_wildcard_listeners", lambda: listeners or {})
    monkeypatch.setattr(network_binding, "_child_map", lambda: kids or {})


def test_discovers_aos_plists_by_label_and_by_aos_path(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [LOOPBACK_PLIST, FOREIGN_PLIST, DEV_PLIST])
    found = network_binding._aos_managed_plists()
    assert "com.aos.bridge" in found, "com.aos.* is AOS-managed"
    assert "com.agent.qareen-dev" in found, "runs from an AOS venv — in scope"
    assert "com.hish.caddy" not in found, "operator's own service — must not be flagged"


def test_clean_install_passes(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [LOOPBACK_PLIST])
    _stub_runtime(monkeypatch, pids={"com.aos.bridge": 100})
    assert NetworkBindingCheck().check() is True


def test_live_wildcard_listener_fails(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [LOOPBACK_PLIST])
    _stub_runtime(
        monkeypatch,
        pids={"com.aos.bridge": 100},
        listeners={100: ["*:4096"]},
    )
    c = NetworkBindingCheck()
    assert c.check() is False
    assert any("com.aos.bridge" in r and "*:4096" in r for r in c._runtime)


def test_wildcard_in_child_process_is_caught(monkeypatch, tmp_path):
    """uvicorn --reload binds in a worker child; checking the job PID alone
    would give an exposed service a clean bill of health."""
    _agents(monkeypatch, tmp_path, [LOOPBACK_PLIST])
    _stub_runtime(
        monkeypatch,
        pids={"com.aos.bridge": 100},
        listeners={222: ["*:4096"]},   # grandchild holds the socket
        kids={100: [111], 111: [222]},
    )
    c = NetworkBindingCheck()
    assert c.check() is False
    assert c._runtime, "descendant socket must be attributed to the job"


def test_duplicate_address_across_parent_and_child_reported_once(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [LOOPBACK_PLIST])
    _stub_runtime(
        monkeypatch,
        pids={"com.aos.bridge": 100},
        listeners={100: ["*:4097"], 111: ["*:4097"]},
        kids={100: [111]},
    )
    c = NetworkBindingCheck()
    c.check()
    assert len(c._runtime) == 1
    assert c._runtime[0].count("*:4097") == 1, "same socket must not double-report"


def test_foreign_service_wildcard_ignored(monkeypatch, tmp_path):
    """caddy on 0.0.0.0 is intentional and not ours to police."""
    _agents(monkeypatch, tmp_path, [FOREIGN_PLIST])
    _stub_runtime(
        monkeypatch,
        pids={"com.hish.caddy": 100},
        listeners={100: ["*:8088"]},
    )
    assert NetworkBindingCheck().check() is True


def test_declared_wildcard_in_stopped_service_fails(monkeypatch, tmp_path):
    """Not running now, but exposes itself the moment it starts."""
    _agents(monkeypatch, tmp_path, [WILDCARD_PLIST])
    _stub_runtime(monkeypatch, pids={})  # not running
    c = NetworkBindingCheck()
    assert c.check() is False
    assert c._declared and not c._runtime


def test_allowlist_excuses_a_deliberate_bind(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [WILDCARD_PLIST])
    (tmp_path / "network.yaml").write_text(
        "allow_wildcard_bind:\n"
        "  - label: com.aos.qareen\n"
        "    reason: deliberately LAN-reachable on a trusted network\n"
    )
    _stub_runtime(monkeypatch, pids={"com.aos.qareen": 100}, listeners={100: ["*:4096"]})
    assert NetworkBindingCheck().check() is True


def test_missing_allowlist_reports_everything(monkeypatch, tmp_path):
    """Absent config must degrade to 'report all', never 'allow all'."""
    _agents(monkeypatch, tmp_path, [WILDCARD_PLIST])
    assert not (tmp_path / "network.yaml").exists()
    _stub_runtime(monkeypatch, pids={"com.aos.qareen": 100}, listeners={100: ["*:4096"]})
    assert NetworkBindingCheck().check() is False


def test_no_agents_dir_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(network_binding, "LAUNCH_AGENTS", tmp_path / "nope")
    monkeypatch.setattr(network_binding, "ALLOW_CONFIG", tmp_path / "network.yaml")
    c = NetworkBindingCheck()
    assert c.check() is True
    assert c.fix().status is Status.SKIP


def test_fix_notifies_and_never_repairs(monkeypatch, tmp_path):
    _agents(monkeypatch, tmp_path, [WILDCARD_PLIST])
    _stub_runtime(monkeypatch, pids={"com.aos.qareen": 100}, listeners={100: ["*:4096"]})
    c = NetworkBindingCheck()
    assert c.check() is False
    result = c.fix()
    assert result.status is Status.NOTIFY, "must never claim FIXED — rebinding restarts services"
    assert result.notify is True
    assert "4096" in result.detail
    assert "tailscale serve" in result.detail
    # The plist on disk is untouched by fix().
    with open(tmp_path / "LaunchAgents" / "com.aos.qareen.plist", "rb") as fh:
        assert plistlib.load(fh)["EnvironmentVariables"]["AOS_QAREEN_HOST"] == "0.0.0.0"


def test_periodic_fix_stays_off():
    """Report-only between deploys; migration 095 owns the supervised flip."""
    assert NetworkBindingCheck.periodic_fix is False
