"""Migration 120 — dead MCP entries and the slack-lite orphan — run twice,
against a sandboxed HOME, with launchctl and the obsidian check stubbed.

Same contract as 111-118: check()/up() run twice, the second run changes
nothing, and the live instance is never touched by loading Path.home() before
the module is imported (migrations resolve HOME at module scope).

Part (a) of the migration retires nothing — verified alive on the reference
machine, see the migration's own docstring — so there is no test scenario for
it beyond confirming STALLED_LAUNCHER_LABELS stays empty and up() never calls
launchctl bootout for any of the four names.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"


def load_migration(name: str, home: Path):
    """Import a migration with Path.home() already pointing at the sandbox.

    Mirrors tests/test_migrations_111_116_idempotency.py's loader: migrations
    resolve HOME = Path.home() at module scope, so the patch must be in place
    before exec_module, and the module must be re-imported per test.
    """
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(REPO / "core" / "infra" / "lib"))
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the AOS tree symlinked in, nothing else real."""
    h = tmp_path / "home"
    (h / ".aos" / "config").mkdir(parents=True)
    (h / ".aos" / "services").mkdir(parents=True)
    (h / ".aos" / "backups").mkdir(parents=True)
    (h / "Library" / "LaunchAgents").mkdir(parents=True)
    (h / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


@pytest.fixture
def m(home):
    mod = load_migration("120", home)
    # Never let a test shell out for real — launchctl and the obsidian
    # --check script are both stubbed to "not running / not available" by
    # default; individual tests override _bridge_running / _obsidian_check_passes
    # directly rather than fake subprocess output, since that is what the
    # module actually calls.
    mod._bridge_running = lambda: False
    mod._obsidian_check_passes = lambda: None
    return mod


def _fail_if_called(*args, **kwargs):  # pragma: no cover - safety net
    raise AssertionError(f"subprocess.run must be stubbed in tests, got: {args}")


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fail_if_called)


# ── (a) nothing retired ─────────────────────────────────────────────────────


def test_a_retires_nothing(m):
    assert m.STALLED_LAUNCHER_LABELS == ()


def test_a_up_never_touches_launchctl(m, home):
    # No LaunchAgents at all in the sandbox; up() must still succeed and must
    # not attempt to boot anything out (the autouse fixture would raise if it
    # tried to shell out for a real launchctl call for these labels).
    assert m.up() is True


# ── (b) orphaned instance service dirs ──────────────────────────────────────


def _make_service_dir(home: Path, name: str) -> Path:
    d = home / ".aos" / "services" / name
    d.mkdir(parents=True)
    (d / "marker.txt").write_text("keep me")
    return d


def test_b_archives_slack_lite_then_is_a_noop(m, home):
    _make_service_dir(home, "slack-lite")
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    archived = list((home / ".aos" / "backups" / "retired-services").glob("slack-lite-*"))
    assert len(archived) == 1
    assert (archived[0] / "marker.txt").read_text() == "keep me"
    assert not (home / ".aos" / "services" / "slack-lite").exists()

    # Second run: nothing left to archive, no duplicate archive created.
    assert m.up() is True
    archived_again = list((home / ".aos" / "backups" / "retired-services").glob("slack-lite-*"))
    assert len(archived_again) == 1


def test_b_extractd_is_never_a_candidate(m):
    """extractd is excluded at the module level — verified alive, see docstring."""
    assert "extractd" not in m.ORPHAN_SERVICE_DIRS


def test_b_leaves_a_dir_alone_if_a_plist_references_it(m, home):
    _make_service_dir(home, "slack-lite")
    la = home / "Library" / "LaunchAgents" / "am.hish.something.plist"
    la.write_text(
        "<plist><string>/Users/x/.aos/services/slack-lite/run.py</string></plist>"
    )
    assert m.check() is True  # nothing this migration is willing to touch
    assert m.up() is True
    assert (home / ".aos" / "services" / "slack-lite").exists()


def test_b_nothing_present_is_a_noop(m):
    assert m.check() is True
    assert m.up() is True


# ── (c) dead mcpServers entries ──────────────────────────────────────────────


def _write_claude_json(home: Path, servers: dict, projects: dict | None = None) -> Path:
    f = home / ".claude.json"
    data = {"mcpServers": servers}
    if projects is not None:
        data["projects"] = projects
    f.write_text(json.dumps(data, indent=2))
    return f


def test_c_removes_all_three_then_is_a_noop(m, home):
    _write_claude_json(home, {
        "memory": {"type": "stdio", "command": "/x"},
        "crawler": {"type": "stdio", "command": "/y"},
        "xcode": {"type": "stdio", "command": "xcrun"},
        "qmd": {"type": "stdio", "command": "/z"},
    })
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    config = json.loads((home / ".claude.json").read_text())
    assert set(config["mcpServers"]) == {"qmd"}


def test_c_writes_a_timestamped_backup_before_editing(m, home):
    original = _write_claude_json(home, {"memory": {"type": "stdio", "command": "/x"}})
    original_text = original.read_text()
    m.up()
    backups = list(home.glob(".claude.json.bak-120-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == original_text


def test_c_leaves_per_project_mcp_servers_alone(m, home):
    _write_claude_json(
        home,
        {"memory": {"type": "stdio", "command": "/x"}},
        projects={
            "/Users/x/some-project": {
                "mcpServers": {"memory": {"type": "stdio", "command": "/keep-me"}}
            }
        },
    )
    m.up()
    config = json.loads((home / ".claude.json").read_text())
    assert "memory" not in config["mcpServers"]
    assert config["projects"]["/Users/x/some-project"]["mcpServers"]["memory"] == {
        "type": "stdio", "command": "/keep-me"
    }


def test_c_no_file_is_a_noop(m):
    assert m.check() is True
    assert m.up() is True


def test_c_no_dead_entries_is_a_noop_and_writes_no_backup(m, home):
    _write_claude_json(home, {"qmd": {"type": "stdio", "command": "/z"}})
    assert m.check() is True
    m.up()
    assert not list(home.glob(".claude.json.bak-120-*"))


# ── (d) integrations.yaml status drift ───────────────────────────────────────


def _write_integrations(home: Path, body: str) -> Path:
    f = home / ".aos" / "config" / "integrations.yaml"
    f.write_text(body)
    return f


def test_d_corrects_telegram_when_bridge_is_verified_running(m, home):
    _write_integrations(home, "integrations:\n  telegram:\n    status: failed\n")
    m._bridge_running = lambda: True
    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["telegram"]["status"] == "active"
    assert "note" in data["integrations"]["telegram"]

    # Second run: already corrected, stays a no-op even though the bridge
    # check still returns True.
    before = (home / ".aos" / "config" / "integrations.yaml").read_text()
    m.up()
    assert (home / ".aos" / "config" / "integrations.yaml").read_text() == before


def test_d_leaves_telegram_failed_when_bridge_is_not_running(m, home):
    _write_integrations(home, "integrations:\n  telegram:\n    status: failed\n")
    m._bridge_running = lambda: False
    assert m.check() is True
    m.up()
    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["telegram"]["status"] == "failed"


def test_d_corrects_obsidian_only_when_check_passes(m, home):
    _write_integrations(home, "integrations:\n  obsidian:\n    status: failed\n")
    m._obsidian_check_passes = lambda: True
    assert m.check() is False
    m.up()
    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["obsidian"]["status"] == "active"


def test_d_leaves_obsidian_failed_when_check_fails(m, home):
    _write_integrations(home, "integrations:\n  obsidian:\n    status: failed\n")
    m._obsidian_check_passes = lambda: False
    assert m.check() is True
    m.up()
    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["obsidian"]["status"] == "failed"


def test_d_leaves_obsidian_failed_when_check_is_unavailable(m, home):
    _write_integrations(home, "integrations:\n  obsidian:\n    status: failed\n")
    m._obsidian_check_passes = lambda: None
    assert m.check() is True
    m.up()
    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["obsidian"]["status"] == "failed"


def test_d_preserves_other_integrations(m, home):
    _write_integrations(
        home,
        "integrations:\n"
        "  telegram:\n    status: failed\n"
        "  github:\n    status: active\n"
        "  google_suite:\n    status: active\n"
        "    accounts:\n      - a@example.com\n",
    )
    m._bridge_running = lambda: True
    m.up()
    import yaml
    data = yaml.safe_load((home / ".aos" / "config" / "integrations.yaml").read_text())
    assert data["integrations"]["github"]["status"] == "active"
    assert data["integrations"]["google_suite"]["accounts"] == ["a@example.com"]


def test_d_no_file_is_a_noop(m):
    assert m.check() is True
    assert m.up() is True


# ── full run: everything together, twice ────────────────────────────────────


def test_full_migration_twice_is_stable(m, home):
    _make_service_dir(home, "slack-lite")
    _write_claude_json(home, {
        "memory": {"type": "stdio", "command": "/x"},
        "crawler": {"type": "stdio", "command": "/y"},
        "xcode": {"type": "stdio", "command": "xcrun"},
    })
    _write_integrations(home, "integrations:\n  telegram:\n    status: failed\n")
    m._bridge_running = lambda: True

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    # Idempotent: second run changes nothing further.
    claude_before = (home / ".claude.json").read_text()
    integrations_before = (home / ".aos" / "config" / "integrations.yaml").read_text()
    assert m.up() is True
    assert (home / ".claude.json").read_text() == claude_before
    assert (home / ".aos" / "config" / "integrations.yaml").read_text() == integrations_before


# ── The live instance is never touched ───────────────────────────────────────


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard — see the equivalent test in
    tests/test_migrations_111_116_idempotency.py for why this matters: a
    Path.home() patch that leaked out of a test would mean every assertion
    above ran against (and mutated) the operator's own machine instead of a
    tmp_path sandbox.

    This suite has no single global side-effect file whose absence it could
    check (~/.aos/backups/retired-services could legitimately already exist on
    a machine that ran this migration for real) — so the property this test actually guards is the one every other
    test in the file depends on: the patch stays scoped to `load_migration`'s
    `try/finally` and never survives past it.
    """
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"
