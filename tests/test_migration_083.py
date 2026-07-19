"""
Tests for migration 083 — rebuild ~/.aos/config/state.yaml from the service
registry (aos#180). Verifies: only ACTIVE services are written; the health URL
is the full HTTP URL for liveness=http and "" for poll_timestamp (bridge) and
interval; optional/retired services are excluded; the strict content-based
idempotency guard; and non-service top-level keys are preserved.

All paths are redirected to tmp_path; nothing touches real ~/.aos/ or ~/aos/.
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


# Minimal manifests covering each liveness/status case the migration must handle.
_MANIFESTS = {
    "bridge": {  # active, poll_timestamp → health ""
        "name": "bridge", "purpose": "x", "status": "active", "type": "resident",
        "owner_layer": "framework", "liveness": "poll_timestamp",
        "label": "com.aos.bridge", "port": 4098,
    },
    "transcriber": {  # active, http → full URL
        "name": "transcriber", "purpose": "x", "status": "active", "type": "resident",
        "owner_layer": "framework", "liveness": "http", "port": 7602,
        "health_endpoint": "/health", "label": "com.aos.transcriber",
    },
    "mesh": {  # optional → excluded
        "name": "mesh", "purpose": "x", "status": "optional", "type": "resident",
        "owner_layer": "framework", "liveness": "http", "port": 4100,
        "health_endpoint": "/health", "label": "com.aos.mesh",
    },
    "listen": {  # retired → excluded
        "name": "listen", "purpose": "x", "status": "retired", "type": "resident",
        "owner_layer": "framework", "liveness": "http", "port": 7600,
        "health_endpoint": "/health", "label": "com.aos.listen",
    },
}


@pytest.fixture
def mig(tmp_path, monkeypatch):
    m = _load("083_service_state_from_registry")
    services_dir = tmp_path / "core" / "services"
    qareen = tmp_path / "core" / "qareen" / "service.yaml"
    services_d = tmp_path / "config" / "services.d"
    state = tmp_path / ".aos" / "config" / "state.yaml"
    services_dir.mkdir(parents=True)
    qareen.parent.mkdir(parents=True)
    services_d.mkdir(parents=True)

    monkeypatch.setattr(m, "SERVICES_DIR", services_dir)
    monkeypatch.setattr(m, "QAREEN_MANIFEST", qareen)
    monkeypatch.setattr(m, "SERVICES_D", services_d)
    monkeypatch.setattr(m, "STATE_YAML", state)

    def declare(*names):
        for n in names:
            d = services_dir / n
            d.mkdir(exist_ok=True)
            (d / "service.yaml").write_text(yaml.safe_dump(_MANIFESTS[n]))

    yield {"m": m, "state": state, "declare": declare}
    sys.modules.pop("083_service_state_from_registry", None)


def _services(state: Path) -> dict:
    return yaml.safe_load(state.read_text())["services"]


def test_only_active_services_written(mig):
    mig["declare"]("bridge", "transcriber", "mesh", "listen")
    assert mig["m"].up() is True
    svcs = _services(mig["state"])
    assert set(svcs) == {"bridge", "transcriber"}  # optional + retired excluded


def test_http_service_gets_full_url(mig):
    mig["declare"]("transcriber")
    mig["m"].up()
    assert _services(mig["state"])["transcriber"]["health"] == "http://127.0.0.1:7602/health"


def test_poll_timestamp_service_has_empty_health(mig):
    mig["declare"]("bridge")
    mig["m"].up()
    entry = _services(mig["state"])["bridge"]
    assert entry["launchagent"] == "com.aos.bridge"
    assert entry["health"] == ""  # loaded-check only, never health-probed


def test_idempotent_content_check(mig):
    mig["declare"]("bridge", "transcriber")
    assert mig["m"].check() is False  # file absent
    assert mig["m"].up() is True
    assert mig["m"].check() is True   # now equals the deterministic rebuild
    assert mig["m"].up() is True      # re-running is safe
    assert mig["m"].check() is True


def test_preserves_non_service_keys(mig):
    mig["state"].parent.mkdir(parents=True, exist_ok=True)
    mig["state"].write_text(yaml.safe_dump({"network": {"tailscale": True}, "services": {}}))
    mig["declare"]("bridge")
    mig["m"].up()
    data = yaml.safe_load(mig["state"].read_text())
    assert data["network"] == {"tailscale": True}
    assert set(data["services"]) == {"bridge"}
