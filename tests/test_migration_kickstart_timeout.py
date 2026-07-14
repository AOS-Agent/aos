"""
Tests for the kickstart-timeout drain-blocking fix in migrations
054_qareen_service.py and 056_n8n_service.py.

`launchctl kickstart -k <service>` can block past a short subprocess
timeout while an old instance drains before the new one binds its port.
Previously up() called `_run([...kickstart...], timeout=10)` unguarded —
subprocess.TimeoutExpired propagated out of up(), the migration runner's
generic `except Exception` caught it, and the migration was logged as
failed even though the service came up healthy seconds later (observed
live during wave 3's edge deployment: kickstart timed out, n8n was
healthy seconds after). The fix: catch TimeoutExpired around the
kickstart call and continue to the health poll, which is the real
success criterion.

Loads each migration module directly (same pattern as
test_migration_runner.py) with all its I/O-touching module-level state
redirected to tmp_path and its I/O functions monkeypatched, so nothing
here touches ~/.aos/, npm, pip, or launchctl.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent / "core" / "infra" / "migrations"


def _load_module(name: str):
    path = MIGRATIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_run(kickstart_raises: bool, default_timeout: int):
    """Build a fake `_run` matching each migration's `_run(cmd, timeout=...)`
    signature: succeeds for everything except an optional raise on the
    `launchctl kickstart` call, mirroring a drain-blocking timeout.
    """
    def run(cmd, timeout=default_timeout):
        if kickstart_raises and cmd[:2] == ["launchctl", "kickstart"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return run


class TestN8nKickstartTimeout:
    """056_n8n_service.py up() must not fail when kickstart -k times out."""

    @pytest.fixture
    def mod(self, tmp_path, monkeypatch):
        m = _load_module("056_n8n_service")

        m.N8N_DATA_DIR = tmp_path / "n8n"
        m.N8N_CONFIG_DIR = m.N8N_DATA_DIR / ".n8n"
        m.LOG_DIR = tmp_path / "logs"
        m.PLIST_PATH = tmp_path / "com.aos.n8n.plist"
        m.TEMPLATE_PATH = tmp_path / "com.aos.n8n.plist.template"
        m.TEMPLATE_PATH.write_text("__HOME__")

        monkeypatch.setattr(m, "_has_n8n", lambda: True)
        monkeypatch.setattr(m, "_has_api_key", lambda: True)
        monkeypatch.setattr(m, "_port_open", lambda *a, **k: False)
        monkeypatch.setattr(m.time, "sleep", lambda s: None)

        yield m
        sys.modules.pop("056_n8n_service", None)

    def test_kickstart_timeout_is_not_fatal_and_health_poll_still_runs(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_run", _fake_run(kickstart_raises=True, default_timeout=10))
        monkeypatch.setattr(mod, "_is_healthy", lambda: True)

        assert mod.up() is True

    def test_kickstart_timeout_then_health_never_comes_up_still_returns_true(self, mod, monkeypatch):
        """up() intentionally returns True even if health never arrives —
        the reconcile check owns ongoing monitoring, not the migration.
        Pins that the timeout-catch doesn't change that existing contract."""
        monkeypatch.setattr(mod, "_run", _fake_run(kickstart_raises=True, default_timeout=10))
        monkeypatch.setattr(mod, "_is_healthy", lambda: False)

        assert mod.up() is True

    def test_no_timeout_still_succeeds_normally(self, mod, monkeypatch):
        """Baseline: behavior is unchanged when kickstart doesn't time out."""
        monkeypatch.setattr(mod, "_run", _fake_run(kickstart_raises=False, default_timeout=10))
        monkeypatch.setattr(mod, "_is_healthy", lambda: True)

        assert mod.up() is True


class TestQareenKickstartTimeout:
    """054_qareen_service.py up() has the identical unguarded kickstart -k
    pattern (found via the 050-059 sweep) and must not fail when it times
    out either."""

    @pytest.fixture
    def mod(self, tmp_path, monkeypatch):
        m = _load_module("054_qareen_service")

        m.QAREEN_VENV = tmp_path / "venv"
        m.QAREEN_PYTHON = m.QAREEN_VENV / "bin" / "python"
        (m.QAREEN_VENV / "bin").mkdir(parents=True)
        m.QAREEN_PYTHON.write_text("")  # only .exists() is checked, never executed

        m.REQUIREMENTS = tmp_path / "requirements.txt"
        m.REQUIREMENTS.write_text("")
        m.SCHEMA_SQL = tmp_path / "does_not_exist.sql"
        m.DB_PATH = tmp_path / "data" / "qareen.db"
        m.MODELS_DIR = tmp_path / "models"
        m.LOG_DIR = tmp_path / "logs"
        m.PLIST_PATH = tmp_path / "com.aos.qareen.plist"
        m.TEMPLATE_PATH = tmp_path / "com.aos.qareen.plist.template"
        m.TEMPLATE_PATH.write_text("__HOME__")

        monkeypatch.setattr(m.time, "sleep", lambda s: None)

        yield m
        sys.modules.pop("054_qareen_service", None)

    def test_kickstart_timeout_is_not_fatal_and_health_poll_still_runs(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_run", _fake_run(kickstart_raises=True, default_timeout=120))
        monkeypatch.setattr(mod, "_is_healthy", lambda: True)

        assert mod.up() is True

    def test_no_timeout_still_succeeds_normally(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_run", _fake_run(kickstart_raises=False, default_timeout=120))
        monkeypatch.setattr(mod, "_is_healthy", lambda: True)

        assert mod.up() is True
