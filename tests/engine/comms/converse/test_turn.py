"""Unit tests for converse/turn.py — the strict JSON handler-output parser
and the fail-safe run_turn() wrapper. No real `claude` subprocess is ever
spawned: subprocess.run is monkeypatched throughout.
"""

from __future__ import annotations

import json

import pytest

from core.engine.comms.converse import models, turn


def _envelope(result_obj: dict | str) -> str:
    """Build a fake `claude --output-format json` envelope whose `result`
    field is either a raw string (already the handler JSON) or a dict
    (json.dumps'd for convenience)."""
    result = result_obj if isinstance(result_obj, str) else json.dumps(result_obj)
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result})


VALID_REPLY = {
    "action": "reply",
    "message": "Sounds good, I'll follow up tomorrow.",
    "state_summary": "Contact confirmed availability for Tuesday.",
    "confidence": "high",
    "reason": "direct answer to their question",
}

VALID_WAIT = {
    "action": "wait",
    "state_summary": "Waiting on contact to confirm a time.",
}

VALID_COMPLETE = {
    "action": "complete",
    "message": "Great, all set — thanks!",
    "state_summary": "Mission done: meeting confirmed.",
    "confidence": "high",
    "summary": "Contact confirmed the meeting time.",
}

VALID_ESCALATE = {
    "action": "escalate",
    "state_summary": "Contact asked about payment — escalating.",
    "summary": "Contact wants to discuss payment terms.",
}


# ---------------------------------------------------------------------------
# parse_turn_output — valid contract shapes
# ---------------------------------------------------------------------------


def test_parse_valid_reply():
    parsed = turn.parse_turn_output(_envelope(VALID_REPLY))
    assert parsed.action == "reply"
    assert parsed.message == VALID_REPLY["message"]
    assert parsed.confidence == "high"
    assert parsed.propose_actions == []


def test_parse_valid_wait_no_message_needed():
    parsed = turn.parse_turn_output(_envelope(VALID_WAIT))
    assert parsed.action == "wait"
    assert parsed.message is None


def test_parse_valid_complete_requires_summary():
    parsed = turn.parse_turn_output(_envelope(VALID_COMPLETE))
    assert parsed.action == "complete"
    assert parsed.summary


def test_parse_valid_escalate_without_message():
    parsed = turn.parse_turn_output(_envelope(VALID_ESCALATE))
    assert parsed.action == "escalate"
    assert parsed.message is None
    assert parsed.summary


def test_parse_handles_prose_wrapped_json():
    """The model often wraps the JSON in a sentence or two — the
    brace-matcher must still find it (this is what the envoy prototype's
    parse_action already proved)."""
    prose = "Here is my decision:\n" + json.dumps(VALID_REPLY) + "\nDone."
    parsed = turn.parse_turn_output(_envelope(prose))
    assert parsed.action == "reply"


def test_parse_handles_braces_inside_message_string():
    """Extension over the original envoy brace-matcher: a message
    containing literal '{'/'}' must not desync the depth counter."""
    obj = dict(VALID_REPLY)
    obj["message"] = "Sure — the code is {1234}, use it before Friday."
    parsed = turn.parse_turn_output(_envelope(obj))
    assert parsed.message == obj["message"]


def test_parse_propose_actions_human_touchpoint():
    obj = dict(VALID_REPLY)
    obj["propose_actions"] = [
        {"kind": "human_touchpoint", "description": "Update row 4 of the sheet", "artifact_url": "https://x"}
    ]
    parsed = turn.parse_turn_output(_envelope(obj))
    assert len(parsed.propose_actions) == 1
    assert parsed.propose_actions[0]["kind"] == "human_touchpoint"


def test_state_summary_truncated_not_rejected():
    obj = dict(VALID_WAIT)
    obj["state_summary"] = "x" * 3000
    parsed = turn.parse_turn_output(_envelope(obj))
    assert len(parsed.state_summary) == turn.MAX_STATE_SUMMARY_CHARS


# ---------------------------------------------------------------------------
# parse_turn_output — fail-safe malformed cases (must raise TurnParseError)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not json at all {",
        "[]",  # valid JSON but not an object envelope
    ],
)
def test_parse_rejects_bad_envelope(raw):
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


def test_parse_rejects_is_error_envelope():
    raw = json.dumps({"is_error": True, "result": "boom"})
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


def test_parse_rejects_missing_result_field():
    raw = json.dumps({"type": "result"})
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


def test_parse_rejects_no_json_object_in_result():
    raw = _envelope("I decided to reply but forgot to output JSON.")
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


def test_parse_rejects_unbalanced_braces():
    raw = _envelope('{"action": "reply", "message": "hi"')  # truncated, no closing brace
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


def test_parse_rejects_invalid_action():
    obj = dict(VALID_REPLY)
    obj["action"] = "delete_everything"
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_missing_state_summary():
    obj = dict(VALID_REPLY)
    del obj["state_summary"]
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_empty_state_summary():
    obj = dict(VALID_REPLY)
    obj["state_summary"] = "   "
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_reply_without_message():
    obj = {"action": "reply", "state_summary": "s", "confidence": "high"}
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_message_without_confidence():
    obj = {"action": "reply", "message": "hi", "state_summary": "s"}
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_invalid_confidence_value():
    obj = dict(VALID_REPLY)
    obj["confidence"] = "very high"
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_complete_without_summary():
    obj = {"action": "complete", "state_summary": "s", "message": "done", "confidence": "high"}
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_escalate_without_summary():
    obj = {"action": "escalate", "state_summary": "s"}
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_propose_action_send_reply_kind():
    """send_reply is gate-owned — the handler must not be able to
    self-propose the one action kind that bypasses the gate's routing."""
    obj = dict(VALID_REPLY)
    obj["propose_actions"] = [{"kind": "send_reply", "description": "just send it"}]
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_propose_action_missing_description():
    obj = dict(VALID_REPLY)
    obj["propose_actions"] = [{"kind": "human_touchpoint"}]
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(_envelope(obj))


def test_parse_rejects_non_object_top_level():
    raw = _envelope(json.dumps(["not", "an", "object"]))
    with pytest.raises(turn.TurnParseError):
        turn.parse_turn_output(raw)


# ---------------------------------------------------------------------------
# run_turn — fail-safe subprocess wrapper (subprocess.run monkeypatched)
# ---------------------------------------------------------------------------


def _session(**overrides) -> models.ConversationSession:
    base = dict(
        id="cs_test0000001",
        mode=models.MODE_ENVOY,
        voice=models.VOICE_AGENT,
        channel=models.CHANNEL_IMESSAGE,
        conversation_ref="ref",
        counterpart_handle="+15550001111",
        mission="Confirm the delivery address.",
        status=models.STATUS_ACTIVE,
        created_at=0,
        updated_at=0,
        tools=models.TOOLS_NONE,
        trust_level=2,
    )
    base.update(overrides)
    return models.ConversationSession(**base)


def _msg(text: str, *, role=models.ROLE_CONTACT, direction=models.DIRECTION_INBOUND, mid="sm_1") -> models.SessionMessage:
    return models.SessionMessage(
        id=mid, session_id="cs_test0000001", role=role, direction=direction,
        text=text, state=models.MSG_HANDLING, ts="2026-08-05T00:00:00+00:00", created_at=0,
    )


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_turn_success(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None, env=None):
        return _FakeProc(returncode=0, stdout=_envelope(VALID_REPLY))

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session()
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is True
    assert outcome.parsed.action == "reply"
    assert outcome.error is None


def test_run_turn_nonzero_exit_is_safe_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session()
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is False
    assert outcome.parsed is None
    assert "rc=1" in outcome.error


def test_run_turn_timeout_is_safe_failure(monkeypatch):
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session()
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is False
    assert "timed out" in outcome.error


def test_run_turn_malformed_output_is_safe_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=0, stdout="not json")

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session()
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is False
    assert outcome.parsed is None
    assert "unparseable" in outcome.error


def test_run_turn_launch_exception_is_safe_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no claude binary")

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session()
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is False
    assert "failed to launch" in outcome.error


def test_build_command_none_profile_has_empty_allowed_tools():
    session = _session(tools=models.TOOLS_NONE)
    cmd = turn.build_command(session, claude_bin="claude")
    i = cmd.index("--allowedTools")
    assert cmd[i + 1] == ""


def test_build_command_full_profile_has_max_turns():
    session = _session(tools=models.TOOLS_FULL)
    cmd = turn.build_command(session, claude_bin="claude")
    assert "--max-turns" in cmd
    assert "40" in cmd


def test_run_turn_full_profile_creates_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(turn, "WORKSPACE_ROOT", tmp_path)

    captured = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None, env=None):
        captured["cwd"] = cwd
        return _FakeProc(returncode=0, stdout=_envelope(VALID_WAIT))

    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    session = _session(tools=models.TOOLS_FULL, id="cs_workspace01")
    outcome = turn.run_turn(session, [_msg("hi")], [_msg("hi")])
    assert outcome.ok is True
    assert captured["cwd"] == str(tmp_path / "cs_workspace01")
    assert (tmp_path / "cs_workspace01").is_dir()
