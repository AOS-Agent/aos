"""
Tests for the `cmux_socket_control` reconcile check's NOTIFY message
(core/infra/reconcile/checks/cmux_socket_control.py).

The faisal-mini parity audit (aos#237) found this check NOTIFYing with a
message that assumed cmux was properly installed and just needed a config
nudge — "Set automation.socketControlMode to ... then run `cmux
reload-config`" — when the real problem was that only the brew CLI formula
was on PATH and /Applications/cmux.app (the .app bundle `aos start` actually
needs) was never installed. The CLI alone can satisfy `_cmux_present()`
(`shutil.which("cmux")`), so the check ran, found no usable config to edit,
and gave advice that doesn't apply to a machine with no cmux app at all.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK_PATH = REPO / "core" / "infra" / "reconcile" / "checks" / "cmux_socket_control.py"


def load_check_module(home: Path):
    """Import the check with Path.home() pointing at a sandbox.

    `infra.cmux_config`'s CONFIG_FILE is `Path.home() / ".config" / "cmux" /
    "cmux.json"`, resolved at import time — so both the patch and dropping
    any previously-cached `infra`/`infra.cmux_config`/`base` modules have to
    happen before this exec, or a second test in the same process reuses the
    first test's real-HOME-bound module.
    """
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    for name in ("infra", "infra.cmux_config", "base"):
        sys.modules.pop(name, None)
    try:
        loader = importlib.machinery.SourceFileLoader(
            f"cmux_socket_control_{id(home)}", str(CHECK_PATH)
        )
        spec = importlib.util.spec_from_file_location(loader.name, CHECK_PATH, loader=loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]
        for name in ("infra", "infra.cmux_config", "base"):
            sys.modules.pop(name, None)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_app_missing_says_the_app_bundle_is_required_with_the_install_command(home, monkeypatch):
    mod = load_check_module(home)
    # Pinned to a sandbox path rather than the real /Applications, so this
    # test doesn't depend on whether cmux happens to be installed here.
    monkeypatch.setattr(mod, "CMUX_APP_DIR", home / "Applications" / "cmux.app")

    # A brew-CLI-only machine can still have an existing (but unfixable)
    # cmux.json — e.g. from a prior CLI invocation — which is why `ensure()`
    # doesn't just write a fresh valid one and quietly succeed here.
    config_dir = home / ".config" / "cmux"
    config_dir.mkdir(parents=True)
    (config_dir / "cmux.json").write_text("not even json")

    result = mod.CmuxSocketControlCheck().fix()

    assert result.status is mod.Status.NOTIFY
    assert "brew install --cask cmux" in result.detail
    assert ".app" in result.message or ".app" in result.detail
    assert "not enough" in result.detail or "not installed" in result.message


def test_app_present_but_config_unwritable_keeps_the_config_edit_message(home, monkeypatch):
    mod = load_check_module(home)
    app_dir = home / "Applications" / "cmux.app"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "CMUX_APP_DIR", app_dir)

    # A config file ensure() cannot safely edit: no closing brace at all.
    config_dir = home / ".config" / "cmux"
    config_dir.mkdir(parents=True)
    (config_dir / "cmux.json").write_text("not even json")

    result = mod.CmuxSocketControlCheck().fix()

    assert result.status is mod.Status.NOTIFY
    assert "brew install --cask cmux" not in result.detail
    assert "reload-config" in result.detail


def test_already_sufficient_mode_is_ok_not_notify(home, monkeypatch):
    mod = load_check_module(home)
    app_dir = home / "Applications" / "cmux.app"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "CMUX_APP_DIR", app_dir)

    config_dir = home / ".config" / "cmux"
    config_dir.mkdir(parents=True)
    (config_dir / "cmux.json").write_text('{"automation": {"socketControlMode": "automation"}}')

    result = mod.CmuxSocketControlCheck().fix()
    assert result.status is mod.Status.OK
