"""Smoke + contract tests for converse/prompts.py — pure string building,
no I/O. Not full golden-file snapshots (the templates are still expected
to move as the other Wave-1 builders' work lands), but locks the load-
bearing invariants: both voices render, the disclosure phrase gate.py
checks for is actually present in the agent persona's instructions, the
transcript window is capped, and required sections show up.
"""

from __future__ import annotations

from core.engine.comms.converse import gate, models, prompts


def _session(**overrides) -> models.ConversationSession:
    base = dict(
        id="cs_prompttest01",
        mode=models.MODE_ENVOY,
        voice=models.VOICE_AGENT,
        channel=models.CHANNEL_SLACK,
        conversation_ref="D123",
        counterpart_handle="U123",
        person_name="Sana",
        mission="Coordinate the launch checklist with Sana.",
        success_criteria="Sana confirms the checklist is done.",
        status=models.STATUS_ACTIVE,
        created_at=0,
        updated_at=0,
        tools=models.TOOLS_NONE,
        trust_level=2,
        state_summary=None,
    )
    base.update(overrides)
    return models.ConversationSession(**base)


def _msg(text, mid, *, role=models.ROLE_CONTACT, direction=models.DIRECTION_INBOUND):
    return models.SessionMessage(
        id=mid, session_id="cs_prompttest01", role=role, direction=direction,
        text=text, state=models.MSG_HANDLING, ts="2026-08-05T00:00:00+00:00", created_at=0,
    )


def test_agent_persona_instructs_the_exact_disclosure_phrase_gate_checks_for():
    session = _session(voice=models.VOICE_AGENT)
    p = prompts.build_turn_prompt(session, [_msg("hi", "sm_1")], [_msg("hi", "sm_1")])
    # The literal phrase must appear in the instructions themselves — this
    # is what makes gate.disclosure_present()'s mechanical check reliable.
    assert prompts.DISCLOSURE_PHRASE.lower() in p.lower()
    assert gate.disclosure_present(prompts.DISCLOSURE_PHRASE.join(["Hi, I'm the operator's ", "."]))


def test_operator_persona_never_uses_agent_disclosure_language():
    session = _session(voice=models.VOICE_OPERATOR)
    p = prompts.build_turn_prompt(session, [_msg("hi", "sm_1")], [_msg("hi", "sm_1")])
    assert "never reveal" in p.lower()
    assert "transparency: you are an ai agent" not in p.lower()


def test_output_contract_present_in_both_voices():
    for voice in (models.VOICE_AGENT, models.VOICE_OPERATOR):
        session = _session(voice=voice)
        p = prompts.build_turn_prompt(session, [_msg("hi", "sm_1")], [_msg("hi", "sm_1")])
        assert '"action"' in p
        assert '"state_summary"' in p
        assert "propose_actions" in p


def test_transcript_window_capped_at_30():
    session = _session()
    transcript = [_msg(f"msg {i}", f"sm_{i}") for i in range(50)]
    new_msgs = transcript[-2:]
    p = prompts.build_turn_prompt(session, new_msgs, transcript)
    # Only the most recent 30 should be rendered as the transcript window —
    # the oldest ones (msg 0..19) must not appear.
    assert "msg 0:" not in p and " msg 0\n" not in p
    assert "msg 49" in p


def test_new_messages_marked_in_transcript_and_repeated_in_own_section():
    session = _session()
    transcript = [_msg("earlier", "sm_1"), _msg("just arrived", "sm_2")]
    new_msgs = [transcript[-1]]
    p = prompts.build_turn_prompt(session, new_msgs, transcript)
    assert "[NEW" in p
    assert "New since your last turn" in p
    assert "just arrived" in p


def test_operator_guidance_role_rendered_as_hidden_from_contact():
    session = _session()
    guidance = _msg("mention the discount code", "sm_g", role=models.ROLE_OPERATOR, direction=models.DIRECTION_INTERNAL)
    p = prompts.build_turn_prompt(session, [guidance], [guidance])
    assert "not visible to contact" in p


def test_artifacts_rendered_with_human_touchpoint_guidance():
    session = _session(artifacts='[{"kind": "google_sheet", "url": "https://sheets.example/x", "note": "budget tracker"}]')
    p = prompts.build_turn_prompt(session, [_msg("hi", "sm_1")], [_msg("hi", "sm_1")])
    assert "https://sheets.example/x" in p
    assert "human_touchpoint" in p


def test_missing_state_summary_renders_first_turn_note():
    session = _session(state_summary=None)
    p = prompts.build_turn_prompt(session, [_msg("hi", "sm_1")], [_msg("hi", "sm_1")])
    assert "first turn" in p
