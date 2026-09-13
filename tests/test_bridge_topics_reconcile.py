"""
Tests for the `bridge_topics_config` reconcile check
(core/infra/reconcile/checks/initiatives.py::BridgeTopicsCheck).

Before this change, every NOTIFY this check could produce collapsed to one
vague sentence — "configure Telegram first" — with no command to run. This
pins the exact one-line fix each NOTIFY now points at: `aos bridge-topics
init`, the command added alongside it (core/bin/internal/bridge-topics-init,
wired into core/bin/cli/aos).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK_PATH = REPO / "core" / "infra" / "reconcile" / "checks" / "initiatives.py"


def load_check_module(home: Path):
    """Import initiatives.py with Path.home() pointing at the sandbox.

    CONFIG_PATH/PROJECTS_YAML are class attributes resolved from Path.home()
    at import time, so the patch must be live before exec_module — same
    pattern as the migration tests.
    """
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        loader = importlib.machinery.SourceFileLoader(
            f"initiatives_check_{id(home)}", str(CHECK_PATH)
        )
        spec = importlib.util.spec_from_file_location(loader.name, CHECK_PATH, loader=loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".aos" / "config").mkdir(parents=True)
    return h


def test_notify_when_neither_file_exists_points_at_bridge_topics_init(home):
    mod = load_check_module(home)
    check = mod.BridgeTopicsCheck()

    assert check.check() is False
    result = check.fix()

    assert result.status is mod.Status.NOTIFY
    assert "aos bridge-topics init" in result.message


def test_notify_when_projects_yaml_exists_without_forum_group_id(home):
    (home / ".aos" / "config" / "projects.yaml").write_text("projects: {}\n")

    mod = load_check_module(home)
    check = mod.BridgeTopicsCheck()
    result = check.fix()

    assert result.status is mod.Status.NOTIFY
    assert "aos bridge-topics init" in result.message
    assert "forum_group_id" in result.message


def test_fixes_when_projects_yaml_has_a_forum_group_id(home):
    (home / ".aos" / "config" / "projects.yaml").write_text(
        "system:\n  telegram:\n    forum_group_id: -1001234567890\n"
    )

    mod = load_check_module(home)
    check = mod.BridgeTopicsCheck()
    result = check.fix()

    assert result.status is mod.Status.FIXED
    assert (home / ".aos" / "config" / "bridge-topics.yaml").exists()


def test_check_passes_once_bridge_topics_yaml_exists(home):
    (home / ".aos" / "config" / "bridge-topics.yaml").write_text("topics: {}\n")

    mod = load_check_module(home)
    check = mod.BridgeTopicsCheck()
    assert check.check() is True
