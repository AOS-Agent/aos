"""
Tests for `aos bridge-topics init` (core/bin/internal/bridge-topics-init).

Second-operator machines have neither ~/.aos/config/projects.yaml nor
~/.aos/config/bridge-topics.yaml, so the bridge quietly runs flat — the only
signal is a log line (core/services/bridge/main.py: "projects.yaml not found
— no topic routes loaded"). The `bridge_topics_config` reconcile check and
`aos self-test` both now point operators at this command as the one-line
fix (see core/infra/reconcile/checks/initiatives.py and core/bin/cli/aos).
These tests cover the command itself, in a sandbox HOME, driven twice like
every other bootstrap in this release.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "internal" / "bridge-topics-init"
REAL_TEMPLATE = REPO / "config" / "templates" / "projects.yaml"


def load_script(home: Path):
    """Import the script with Path.home() already pointing at the sandbox.

    Same pattern as the migration tests: the script resolves HOME/AOS_DIR/
    CONFIG_DIR at module scope, so the patch must be live before exec_module,
    and it must be re-imported per call rather than cached.
    """
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        # SCRIPT has no .py suffix (it's a CLI entry point, not a library),
        # so spec_from_file_location can't infer a loader from the extension
        # — hand it one explicitly.
        loader = importlib.machinery.SourceFileLoader(
            f"bridge_topics_init_{id(home)}", str(SCRIPT)
        )
        spec = importlib.util.spec_from_file_location(
            loader.name, SCRIPT, loader=loader
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path):
    """A sandbox HOME with the real framework template in place, nothing
    else — the same starting point as a fresh second-operator machine."""
    h = tmp_path / "home"
    template_dir = h / "aos" / "config" / "templates"
    template_dir.mkdir(parents=True)
    shutil.copy(REAL_TEMPLATE, template_dir / "projects.yaml")
    (h / ".aos" / "config").mkdir(parents=True)
    return h


def _projects_yaml(home: Path) -> Path:
    return home / ".aos" / "config" / "projects.yaml"


def _bridge_topics_yaml(home: Path) -> Path:
    return home / ".aos" / "config" / "bridge-topics.yaml"


# ── First run: no projects.yaml yet ─────────────────────────────────────────


def test_first_run_scaffolds_projects_yaml_from_the_template(home):
    mod = load_script(home)

    assert not _projects_yaml(home).exists()
    rc = mod.main()

    assert rc == 0
    assert _projects_yaml(home).read_text() == REAL_TEMPLATE.read_text()
    assert not _bridge_topics_yaml(home).exists(), \
        "bridge-topics.yaml must wait for a real forum_group_id"


def test_first_run_never_overwrites_an_existing_projects_yaml(home):
    live = "system:\n  telegram:\n    forum_group_id: -100999\n"
    _projects_yaml(home).write_text(live)

    mod = load_script(home)
    mod.main()

    assert _projects_yaml(home).read_text() == live


# ── Second run: forum_group_id filled in ────────────────────────────────────


def test_second_run_writes_bridge_topics_yaml_with_the_forum_group_id(home):
    _projects_yaml(home).write_text(
        "system:\n  telegram:\n    forum_group_id: -1001234567890\n"
    )

    mod = load_script(home)
    rc = mod.main()

    assert rc == 0
    data = yaml.safe_load(_bridge_topics_yaml(home).read_text())
    assert data["forum_group_id"] == -1001234567890
    assert set(data["topics"].keys()) == {"daily", "alerts", "work", "knowledge", "system"}
    for slot in data["topics"].values():
        assert slot == {"thread_id": None, "created": None, "pinned_message_id": None}


def test_second_run_reads_forum_group_id_from_a_project_entry_too(home):
    _projects_yaml(home).write_text(
        "projects:\n"
        "  my-project:\n"
        "    telegram:\n"
        "      forum_group_id: -1009999999\n"
    )

    mod = load_script(home)
    assert mod.main() == 0
    data = yaml.safe_load(_bridge_topics_yaml(home).read_text())
    assert data["forum_group_id"] == -1009999999


def test_still_fully_commented_template_exits_nonzero_with_guidance(home, capsys):
    # projects.yaml exists (the template, verbatim) but every value is still
    # commented out — nothing for the bootstrap to read yet.
    _projects_yaml(home).write_text(REAL_TEMPLATE.read_text())

    mod = load_script(home)
    rc = mod.main()

    assert rc == 1
    assert not _bridge_topics_yaml(home).exists()
    out = capsys.readouterr().out
    assert "forum_group_id" in out


def test_never_writes_a_bot_token_or_secret(home):
    _projects_yaml(home).write_text(
        "system:\n  telegram:\n    forum_group_id: -1001234567890\n"
    )
    mod = load_script(home)
    mod.main()

    content = _bridge_topics_yaml(home).read_text()
    assert "token" not in content.lower()
    assert "secret" not in content.lower()


# ── Already bootstrapped: pure no-op, never clobbers live thread IDs ────────


def test_already_bootstrapped_is_a_noop_and_keeps_live_thread_ids(home):
    _projects_yaml(home).write_text(
        "system:\n  telegram:\n    forum_group_id: -1001234567890\n"
    )
    live_topics = {
        "forum_group_id": -1001234567890,
        "topics": {"daily": {"thread_id": 42, "created": "2026-01-01", "pinned_message_id": 7}},
        "projects": {},
    }
    _bridge_topics_yaml(home).write_text(yaml.dump(live_topics))

    mod = load_script(home)
    rc = mod.main()

    assert rc == 0
    assert yaml.safe_load(_bridge_topics_yaml(home).read_text()) == live_topics
