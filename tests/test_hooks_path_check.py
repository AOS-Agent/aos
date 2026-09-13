"""
core/infra/reconcile/checks/hooks.py — PostToolUse trust-log-dispatch wiring
(aos#236.4).

chief.md's catalog-dispatch mandate has no enforcement today, which is why
the dangling-wires audit found only 5 trust-log rows ever written. The
PostToolUse hook (core/hooks/trust_log_dispatch.py) makes the write
automatic; this reconcile check is what makes SURE that hook stays
registered in ~/.claude/settings.json across installs and drift — the same
job it already does for SessionStart/SessionEnd.

Never edits the operator's real ~/.claude/settings.json: HooksPathCheck.SETTINGS
is monkeypatched to a scratch file for every test here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "infra" / "reconcile"))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "infra" / "reconcile" / "checks"))

from base import Status  # noqa: E402
from hooks import HooksPathCheck  # noqa: E402


def _base_settings() -> dict:
    """A settings.json that already satisfies every OTHER required hook and
    permission — isolates the assertions to the PostToolUse wiring."""
    return {
        "hooks": {
            "SessionStart": [
                {"hooks": [{
                    "type": "command",
                    "command": "python3 ~/aos/core/engine/work/inject_context.py",
                    "statusMessage": "Loading work context...",
                }]},
            ],
            "SessionEnd": [
                {"hooks": [{
                    "type": "command",
                    "command": "python3 ~/aos/core/engine/work/session_close.py",
                    "async": True,
                }]},
            ],
        },
        "permissions": {"allow": ["Bash", "Read", "Edit", "Write"]},
    }


def test_required_hooks_declares_post_tool_use_trust_log_dispatch():
    spec = HooksPathCheck.REQUIRED_HOOKS["PostToolUse"]
    assert spec["command"] == "python3 ~/aos/core/hooks/trust_log_dispatch.py"
    assert spec["matcher"] == "Agent"


def test_check_fails_when_post_tool_use_hook_is_missing(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_base_settings()))
    monkeypatch.setattr(HooksPathCheck, "SETTINGS", settings_path)

    check = HooksPathCheck()
    assert check.check() is False


def test_fix_adds_post_tool_use_hook_with_matcher(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_base_settings()))
    monkeypatch.setattr(HooksPathCheck, "SETTINGS", settings_path)

    check = HooksPathCheck()
    result = check.fix()
    assert result.status == Status.FIXED, result

    settings = json.loads(settings_path.read_text())
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1
    block = post_tool_use[0]
    assert block["matcher"] == "Agent"
    assert block["hooks"][0]["command"] == "python3 ~/aos/core/hooks/trust_log_dispatch.py"
    assert block["hooks"][0]["async"] is True

    # Re-running now reports the invariant holds.
    assert HooksPathCheck().check() is True


def test_fix_does_not_disturb_an_existing_post_tool_use_entry_for_another_tool(tmp_path, monkeypatch):
    """A PostToolUse hook for some other tool must be preserved, not replaced —
    fix() appends a new matcher block rather than overwriting the event."""
    settings = _base_settings()
    settings["hooks"]["PostToolUse"] = [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "python3 ~/somewhere/other_hook.py"}]},
    ]
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))
    monkeypatch.setattr(HooksPathCheck, "SETTINGS", settings_path)

    check = HooksPathCheck()
    assert check.check() is False  # trust_log_dispatch still missing
    check.fix()

    post_tool_use = json.loads(settings_path.read_text())["hooks"]["PostToolUse"]
    commands = {
        inner["command"]
        for block in post_tool_use
        for inner in block.get("hooks", [])
    }
    assert "python3 ~/somewhere/other_hook.py" in commands
    assert "python3 ~/aos/core/hooks/trust_log_dispatch.py" in commands
