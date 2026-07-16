"""
Tests for migration 082 — rebuild ~/.aos/config/state.yaml from deployed plists
(aos#180). Verifies the discovery, the health-URL attachment (bridge :4098,
transcriber :7602, whatsmeow :7601), the strict content-based idempotency guard,
dead-entry removal, and non-service-key preservation.

All paths are redirected to tmp_path; nothing touches real ~/.aos/ or
~/Library/LaunchAgents/.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

MIGRATIONS_DIR = Path(__file__).parent.parent / "core" / "infra" / "migrations"


def _load(name: str):
    path = MIGRATIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mig(tmp_path, monkeypatch):
    m = _load("082_service_state_rebuild")
    la = tmp_path / "LaunchAgents"
    la.mkdir()
    state = tmp_path / ".aos" / "config" / "state.yaml"
    monkeypatch.setattr(m, "LA_DIR", la)
    monkeypatch.setattr(m, "STATE_YAML", state)

    def deploy(*labels):
        for label in labels:
            (la / f"{label}.plist").write_text("<plist/>")

    yield {"m": m, "la": la, "state": state, "deploy": deploy}
    sys.modules.pop("082_service_state_rebuild", None)


def _load_state(path):
    return yaml.safe_load(path.read_text())


def test_fresh_build_discovers_services_and_health(mig):
    m = mig["m"]
    mig["deploy"]("com.aos.bridge", "com.aos.transcriber", "com.aos.qareen")

    assert m.check() is False  # no file yet
    assert m.up() is True
    assert m.check() is True   # now applied

    svcs = _load_state(mig["state"])["services"]
    assert svcs["bridge"] == {"launchagent": "com.aos.bridge", "health": "http://127.0.0.1:4098/health"}
    assert svcs["transcriber"]["health"] == "http://127.0.0.1:7602/health"
    assert svcs["qareen"]["health"] == "http://127.0.0.1:4096/api/health"


def test_service_without_known_health_gets_empty_string(mig):
    m = mig["m"]
    mig["deploy"]("com.aos.sentinel")
    m.up()
    svcs = _load_state(mig["state"])["services"]
    assert svcs["sentinel"] == {"launchagent": "com.aos.sentinel", "health": ""}


def test_whatsmeow_com_agent_label_is_discovered(mig):
    m = mig["m"]
    mig["deploy"]("com.agent.whatsmeow")
    m.up()
    svcs = _load_state(mig["state"])["services"]
    assert svcs["whatsmeow"] == {
        "launchagent": "com.agent.whatsmeow",
        "health": "http://127.0.0.1:7601/health",
    }


def test_stale_dead_entries_are_replaced(mig):
    m = mig["m"]
    # Pre-existing stale file: dead services + bridge with empty health.
    mig["state"].parent.mkdir(parents=True, exist_ok=True)
    mig["state"].write_text(yaml.safe_dump({"services": {
        "dashboard": {"launchagent": "com.aos.dashboard", "health": ""},
        "phoenix": {"launchagent": "com.aos.phoenix", "health": ""},
        "bridge": {"launchagent": "com.aos.bridge", "health": ""},
    }}))
    # Only bridge is actually deployed now.
    mig["deploy"]("com.aos.bridge")

    assert m.check() is False  # stale content != rebuild
    m.up()
    svcs = _load_state(mig["state"])["services"]
    assert set(svcs) == {"bridge"}                       # dead entries gone
    assert svcs["bridge"]["health"] == "http://127.0.0.1:4098/health"  # bridge got a health URL


def test_idempotent_second_run(mig):
    m = mig["m"]
    mig["deploy"]("com.aos.bridge", "com.aos.n8n")
    m.up()
    first = mig["state"].read_text()
    assert m.check() is True
    m.up()
    assert mig["state"].read_text() == first  # deterministic rewrite


def test_preserves_non_service_top_level_keys(mig):
    m = mig["m"]
    mig["state"].parent.mkdir(parents=True, exist_ok=True)
    mig["state"].write_text(yaml.safe_dump({
        "meta": {"note": "keep me"},
        "services": {"old": {"launchagent": "com.aos.old", "health": ""}},
    }))
    mig["deploy"]("com.aos.bridge")
    m.up()
    data = _load_state(mig["state"])
    assert data["meta"] == {"note": "keep me"}      # untouched
    assert set(data["services"]) == {"bridge"}       # rebuilt


def test_no_plists_yields_empty_services_but_still_succeeds(mig):
    m = mig["m"]
    assert m.up() is True
    assert _load_state(mig["state"])["services"] == {}
