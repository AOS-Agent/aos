"""
BridgeTopicsCheck — surface null forum_topic_id routes (aos#236.5).

core/services/bridge/main.py silently skips any Telegram route whose
`forum_topic_id` is null (`if topic_id is None: continue`) — a project or
system entry can be fully configured except for that one field and the
operator gets no signal the route never loads. Dangling-wires audit
(2026-09-13): "chief project topic 158 — forum_topic_id: null — route
skipped at load."

~/.aos/config/projects.yaml is instance config — this check must never edit
it (the operator sets the real topic ID once the Telegram topic exists).
What it CAN do is NOTIFY with the exact line to fix, so the gap doesn't sit
invisible. This test drives BridgeTopicsCheck.check()/fix() against a
sandboxed projects.yaml and bridge-topics.yaml, never the real instance
config.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "infra" / "reconcile"))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "infra" / "reconcile" / "checks"))

from base import Status  # noqa: E402
from initiatives import BridgeTopicsCheck  # noqa: E402

ACTIVE_PROJECT_WITH_NULL_TOPIC = """
projects:
  chief:
    path: ~/chief-ios-app
    telegram:
      forum_group_id: -1003874890243
      forum_topic_id: null
    agents: auto
    status: active
"""

TECHNICIAN_ENTRY_WITH_NULL_TOPIC = """
projects:
  nuchay:
    path: ~/nuchay
    telegram:
      forum_group_id: -1003874890243
      forum_topic_id: 2
    agents: auto
    status: active

technician:
  telegram:
    forum_group_id: -1003874890243
    forum_topic_id: null
  agents:
    - technician
"""

ALL_ROUTES_RESOLVED = """
projects:
  nuchay:
    path: ~/nuchay
    telegram:
      forum_group_id: -1003874890243
      forum_topic_id: 2
    agents: auto
    status: active
"""

INACTIVE_PROJECT_WITH_NULL_TOPIC = """
projects:
  archived-thing:
    path: ~/archived-thing
    telegram:
      forum_group_id: -1003874890243
      forum_topic_id: null
    agents: auto
    status: archived
"""


def _make_check(tmp_path: Path, projects_yaml: str, bridge_topics_exists: bool = True) -> BridgeTopicsCheck:
    check = BridgeTopicsCheck()
    projects_path = tmp_path / "projects.yaml"
    projects_path.write_text(projects_yaml)
    check.PROJECTS_YAML = projects_path

    bridge_topics_path = tmp_path / "bridge-topics.yaml"
    if bridge_topics_exists:
        bridge_topics_path.write_text("forum_group_id: -1003874890243\ntopics: {}\n")
    check.CONFIG_PATH = bridge_topics_path
    return check


def test_check_fails_when_an_active_project_has_a_null_topic_id(tmp_path):
    check = _make_check(tmp_path, ACTIVE_PROJECT_WITH_NULL_TOPIC)
    assert check.check() is False


def test_check_fails_when_a_non_project_entry_has_a_null_topic_id(tmp_path):
    check = _make_check(tmp_path, TECHNICIAN_ENTRY_WITH_NULL_TOPIC)
    assert check.check() is False


def test_check_passes_when_every_route_has_a_topic_id(tmp_path):
    check = _make_check(tmp_path, ALL_ROUTES_RESOLVED)
    assert check.check() is True


def test_inactive_project_with_null_topic_is_not_flagged(tmp_path):
    check = _make_check(tmp_path, INACTIVE_PROJECT_WITH_NULL_TOPIC)
    assert check.check() is True


def test_fix_notifies_with_the_exact_line_to_fix(tmp_path):
    check = _make_check(tmp_path, ACTIVE_PROJECT_WITH_NULL_TOPIC)
    result = check.fix()

    assert result.status == Status.NOTIFY, result
    assert result.notify is True
    # The exact YAML path the operator needs to edit, and the literal line.
    assert "projects.chief.telegram.forum_topic_id" in result.message
    assert "forum_topic_id:" in result.message
    assert "projects.yaml" in result.message


def test_fix_never_writes_to_projects_yaml(tmp_path):
    check = _make_check(tmp_path, ACTIVE_PROJECT_WITH_NULL_TOPIC)
    before = check.PROJECTS_YAML.read_text()
    check.fix()
    after = check.PROJECTS_YAML.read_text()
    assert before == after, "BridgeTopicsCheck must never edit instance config"


def test_fix_lists_every_broken_route_not_just_the_first(tmp_path):
    combined = ACTIVE_PROJECT_WITH_NULL_TOPIC + "\ntechnician:\n  telegram:\n    forum_group_id: -1003874890243\n    forum_topic_id: null\n  agents:\n    - technician\n"
    check = _make_check(tmp_path, combined)
    result = check.fix()
    assert "chief" in result.message
    assert "technician" in result.message


def test_missing_bridge_topics_yaml_still_takes_priority(tmp_path):
    """Existing scaffold-creation behavior for a genuinely missing config file
    must be unaffected by the new null-topic-route logic."""
    check = _make_check(tmp_path, ALL_ROUTES_RESOLVED, bridge_topics_exists=False)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.FIXED
    assert check.CONFIG_PATH.exists()
