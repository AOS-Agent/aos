"""Migration 124 — work-runner decommission — idempotency and safety.

Same contract as 111–121: up() must be safe to run twice, must never touch the
live instance, and must degrade gracefully when nothing is there to remove.
HOME is redirected before the migration module is imported, since it resolves
every path from Path.home() at module scope.

`launchctl` is never invoked for real: _loaded() is stubbed, because a test that
shells out to the operator's launchd is a test that can boot out a live job.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"


def load_migration(name: str, home: Path):
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        Path.home = real_home  # type: ignore[method-assign]
    # Never talk to the real launchd from a test.
    mod._loaded = lambda: False  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Library" / "LaunchAgents").mkdir(parents=True)
    (h / ".aos" / "config").mkdir(parents=True)
    (h / ".aos" / "launchers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


def _seed_everything(home: Path) -> None:
    (home / "Library" / "LaunchAgents" / "com.aos.work-runner.plist").write_text("<plist/>")
    (home / ".aos" / "launchers" / "work-runner").write_text("#!/bin/sh\n")
    (home / ".aos" / "config" / "work-runner.yaml").write_text("enabled: false\n")
    (home / ".aos" / "config" / "services.yaml").write_text(
        "# header comment\n\ndisabled:\n  - work-runner\n  - n8n\nenabled: []\n"
    )


def _services(home: Path) -> dict:
    return yaml.safe_load((home / ".aos" / "config" / "services.yaml").read_text()) or {}


def test_124_removes_everything_then_is_a_noop(home):
    _seed_everything(home)
    m = load_migration("124", home)

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    assert not (home / "Library" / "LaunchAgents" / "com.aos.work-runner.plist").exists()
    assert not (home / ".aos" / "launchers" / "work-runner").exists()
    assert not (home / ".aos" / "config" / "work-runner.yaml").exists()
    assert "work-runner" not in _services(home).get("disabled", [])

    before = (home / ".aos" / "config" / "services.yaml").read_text()
    assert m.up() is True
    assert (home / ".aos" / "config" / "services.yaml").read_text() == before
    assert m.check() is True


def test_124_preserves_other_service_declarations(home):
    _seed_everything(home)
    load_migration("124", home).up()
    assert _services(home)["disabled"] == ["n8n"]


def test_124_clears_an_explicit_opt_in(home):
    """111 deliberately left an opted-in runner alone. 124 cannot: the code it
    pointed at is gone, so the plist would exec a missing file on a KeepAlive
    loop."""
    _seed_everything(home)
    (home / ".aos" / "config" / "services.yaml").write_text(
        "enabled:\n  - work-runner\n  - sentinel\ndisabled: []\n"
    )
    m = load_migration("124", home)
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    svc = _services(home)
    assert svc["enabled"] == ["sentinel"]
    assert "work-runner" not in svc.get("disabled", [])


def test_124_is_a_noop_on_a_machine_that_never_had_it(home):
    (home / ".aos" / "config" / "services.yaml").write_text("disabled:\n  - n8n\n")
    m = load_migration("124", home)
    assert m.check() is True
    assert m.up() is True
    assert _services(home)["disabled"] == ["n8n"]


def test_124_is_a_noop_with_no_config_at_all(home):
    m = load_migration("124", home)
    assert m.check() is True
    assert m.up() is True


def test_124_leaves_operator_data_alone(home):
    """Logs and runner worktrees are data, not declaration. A migration that
    deletes operator data without being asked is the thing the destructive-ops
    rule exists to stop."""
    _seed_everything(home)
    logs = home / ".aos" / "logs" / "work-runner"
    logs.mkdir(parents=True)
    (logs / "runner.log").write_text("something happened\n")

    load_migration("124", home).up()

    assert (logs / "runner.log").read_text() == "something happened\n"


def test_live_instance_is_untouched_by_this_suite():
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
