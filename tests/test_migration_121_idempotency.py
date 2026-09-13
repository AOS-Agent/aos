"""
Idempotency and safety tests for migration 121 (retire the fleet registry).

Same contract as the other v0.8.0 migrations: up() can run twice. The runner
replays on any machine whose recorded level is behind, a release can be
activated, rolled back and activated again, and an operator can run
`aos migrate` by hand.

121 moves operator-written config out of ~/.aos/config. That makes two failure
modes worth asserting rather than assuming: a replay must not lose the archive
the first run made, and the migration must never touch a config file it was not
named for. Every test runs in a sandbox HOME; nothing reads or writes the live
instance.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"

FLEET_YAML = "nodes:\n  - name: local\n    auto_update: true\n"


def load_migration(name: str, home: Path):
    """Import a migration with Path.home() already pointing at the sandbox.

    Migrations resolve their paths at import time (HOME = Path.home() at module
    scope), so the patch has to be in place before exec_module, and the module
    has to be re-imported per test rather than cached.
    """
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path):
    """A sandbox HOME with an empty ~/.aos/config and nothing else real."""
    h = tmp_path / "home"
    (h / ".aos" / "config").mkdir(parents=True)
    return h


def _archives(home: Path) -> list[Path]:
    d = home / ".aos" / "backups" / "retired-config"
    return sorted(d.iterdir()) if d.exists() else []


# ── The migration does the thing ─────────────────────────────────────────────


def test_121_archives_fleet_yaml_then_is_a_noop(home):
    cfg = home / ".aos" / "config" / "fleet.yaml"
    cfg.write_text(FLEET_YAML)

    m = load_migration("121", home)
    assert m.check() is False
    assert m.up() is True

    # Moved out of config, and the content survived the move.
    assert not cfg.exists()
    archived = _archives(home)
    assert len(archived) == 1
    assert archived[0].name.startswith("fleet.yaml.")
    assert archived[0].read_text() == FLEET_YAML

    # Second run changes nothing.
    assert m.check() is True
    assert m.up() is True
    assert _archives(home) == archived
    assert archived[0].read_text() == FLEET_YAML


def test_121_archives_the_allow_updates_override(home):
    marker = home / ".aos" / "config" / "allow-updates"
    marker.write_text("")

    m = load_migration("121", home)
    assert m.up() is True
    assert not marker.exists()
    assert [p.name.split(".")[0] for p in _archives(home)] == ["allow-updates"]


def test_121_handles_both_files_in_one_run(home):
    (home / ".aos" / "config" / "fleet.yaml").write_text(FLEET_YAML)
    (home / ".aos" / "config" / "allow-updates").write_text("")

    m = load_migration("121", home)
    assert m.up() is True
    assert m.check() is True
    assert len(_archives(home)) == 2


# ── Safety ───────────────────────────────────────────────────────────────────


def test_121_is_already_applied_on_a_machine_that_never_had_the_files(home):
    """The common case: nothing to do, and that is success, not failure."""
    m = load_migration("121", home)
    assert m.check() is True
    assert m.up() is True
    assert _archives(home) == []


def test_121_never_touches_config_it_was_not_named_for(home):
    """No glob over ~/.aos/config — the neighbours are the operator's real config."""
    cfg = home / ".aos" / "config"
    (cfg / "fleet.yaml").write_text(FLEET_YAML)
    neighbours = {
        "operator.yaml": "name: test\n",
        "update-policy.yaml": "frozen: true\n",
        "channel-update.yaml": "forum_topic_id: 88\n",
        "channel": "edge\n",
        "fleet.yaml.bak": "a hand-made copy\n",
    }
    for name, body in neighbours.items():
        (cfg / name).write_text(body)

    m = load_migration("121", home)
    assert m.up() is True

    for name, body in neighbours.items():
        assert (cfg / name).read_text() == body, f"{name} was modified"
    assert not (cfg / "fleet.yaml").exists()


def test_121_replay_after_a_manual_restore_keeps_both_archives(home):
    """An operator who restores the file and replays must not lose the first copy."""
    cfg = home / ".aos" / "config" / "fleet.yaml"
    cfg.write_text(FLEET_YAML)

    m = load_migration("121", home)
    m.up()
    first = _archives(home)
    assert len(first) == 1

    # Operator restores a modified copy by hand, then the runner replays.
    cfg.write_text(FLEET_YAML + "  - name: second\n")
    assert m.check() is False
    m.up()

    after = _archives(home)
    assert len(after) == 2, "the first archive was clobbered"
    assert first[0].read_text() == FLEET_YAML, "the first archive's content changed"


def test_121_reports_a_description_and_refuses_to_reverse(home):
    m = load_migration("121", home)
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION
    assert m.down() is False
