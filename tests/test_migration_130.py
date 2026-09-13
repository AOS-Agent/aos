"""Migration 130 — PostToolUse trust-log-dispatch hook (aos#236.4).

Same contract as migration 085 (the UserPromptSubmit mention_context hook):
appends to ~/.claude/settings.json, preserves everything else, and is
idempotent. The `home` fixture redirects HOME before the module is imported
and holds the patch for the whole test — the module resolves the settings path
from Path.home() on every call.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"

# Captured before any fixture patches it — the value a sandbox must not be.
_REAL_HOME = Path.home()


def load_migration(name: str, home: Path):
    assert Path.home() != _REAL_HOME, (
        "Path.home() still resolves to the operator's real home right before "
        "load_migration() was about to exec a migration module. The calling "
        "test's `home` fixture must patch Path.home() (persistently, for the "
        "whole test) before calling load_migration() — this migration resolves "
        f"its paths per call, so refusing to run {name!r} against the live "
        "instance is the only safe answer."
    )
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
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


def test_check_false_when_settings_missing(home):
    mig = load_migration("130", home)
    assert mig.check() is False


def test_check_false_when_settings_exists_but_hook_missing(home):
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {"SessionStart": []}}))
    mig = load_migration("130", home)
    assert mig.check() is False


def test_up_registers_hook_and_check_then_passes(home):
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "keep-me"}]}]},
    }))
    mig = load_migration("130", home)

    assert mig.up() is True

    settings = json.loads(settings_path.read_text())
    # Pre-existing hooks are untouched.
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "keep-me"

    post = settings["hooks"]["PostToolUse"]
    assert len(post) == 1
    assert post[0]["matcher"] == "Agent"
    assert post[0]["hooks"][0]["command"] == "python3 ~/aos/core/hooks/trust_log_dispatch.py"
    assert post[0]["hooks"][0]["async"] is True

    assert load_migration("130", home).check() is True


def test_up_is_idempotent(home):
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {}}))

    load_migration("130", home).up()
    load_migration("130", home).up()  # second run must not duplicate

    settings = json.loads(settings_path.read_text())
    assert len(settings["hooks"]["PostToolUse"]) == 1
