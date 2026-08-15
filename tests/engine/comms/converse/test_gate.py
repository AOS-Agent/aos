"""Unit tests for converse/gate.py — trust-level routing (L1/L2/L3) and the
hard floor, tested with fakes only (no real comms.db/people.db, no
subprocess, no send). Every evaluate_send() call passes sent_last_hour
and contact_importance_value explicitly so no DB is ever touched.
"""

from __future__ import annotations

from core.engine.comms.converse import gate, models

HARD_FLOOR = {
    "blocked_intent_words": ["book", "schedule", "pay", "buy", "send money", "reserve", "transfer"],
    "block_inner_circle_importance": 1,
    "max_sends_per_hour": 6,
}


def _session(**overrides) -> models.ConversationSession:
    base = dict(
        id="cs_gatetest0001",
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
        sent_count=0,
        max_messages=30,
        expires_at=None,
    )
    base.update(overrides)
    return models.ConversationSession(**base)


DISCLOSED = "Hi, I'm Hisham's AI assistant — following up on the delivery."
UNDISCLOSED = "Hi, following up on the delivery."


# ---------------------------------------------------------------------------
# Hard floor — trust-level independent, including L3
# ---------------------------------------------------------------------------


def test_blocked_intent_word_holds_even_at_trust_3():
    session = _session(trust_level=3, voice=models.VOICE_AGENT)
    decision = gate.evaluate_send(
        session, "Let's book the flight for you.",
        confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert decision.hard_floor_violated is True
    assert any("blocked intent word" in r for r in decision.reasons)


def test_empty_message_holds():
    session = _session(trust_level=3)
    decision = gate.evaluate_send(
        session, "   ", confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("empty message" in r for r in decision.reasons)


def test_rate_limit_holds_even_at_trust_3():
    session = _session(trust_level=3)
    decision = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        sent_last_hour=6, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("rate limit" in r for r in decision.reasons)


def test_under_rate_limit_does_not_hold_for_that_reason():
    session = _session(trust_level=3)
    decision = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        sent_last_hour=5, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


def test_max_messages_cap_holds():
    session = _session(trust_level=3, sent_count=30, max_messages=30)
    decision = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("max_messages cap" in r for r in decision.reasons)


def test_expired_session_holds():
    session = _session(trust_level=3, expires_at=1)  # epoch 1 == long expired
    decision = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("expired" in r for r in decision.reasons)


def test_inner_circle_holds_for_voice_operator_only():
    op_session = _session(voice=models.VOICE_OPERATOR, trust_level=3)
    decision = gate.evaluate_send(
        op_session, "Sounds good!", confidence="high", is_first_outbound=False,
        contact_importance_value=1, sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("inner-circle" in r for r in decision.reasons)

    agent_session = _session(voice=models.VOICE_AGENT, trust_level=3)
    decision2 = gate.evaluate_send(
        agent_session, DISCLOSED, confidence="high", is_first_outbound=True,
        contact_importance_value=1, sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    # voice=agent doesn't get the inner-circle hold — should sail through.
    assert decision2.decision == gate.DECISION_AUTO_SEND


def test_non_inner_circle_importance_does_not_hold():
    session = _session(voice=models.VOICE_OPERATOR, trust_level=3)
    decision = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        contact_importance_value=2, sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


# ---------------------------------------------------------------------------
# Voice rules — disclosure (agent) / relevance (operator)
# ---------------------------------------------------------------------------


def test_agent_first_message_without_disclosure_holds():
    session = _session(voice=models.VOICE_AGENT, trust_level=3)
    decision = gate.evaluate_send(
        session, UNDISCLOSED, confidence="high", is_first_outbound=True,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("AI disclosure" in r for r in decision.reasons)


def test_agent_first_message_with_disclosure_auto_sends_at_trust_3():
    session = _session(voice=models.VOICE_AGENT, trust_level=3)
    decision = gate.evaluate_send(
        session, DISCLOSED, confidence="high", is_first_outbound=True,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


def test_agent_second_message_does_not_need_disclosure():
    session = _session(voice=models.VOICE_AGENT, trust_level=3)
    decision = gate.evaluate_send(
        session, UNDISCLOSED, confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


def test_operator_first_reply_with_no_keyword_overlap_holds():
    session = _session(voice=models.VOICE_OPERATOR, trust_level=3)
    decision = gate.evaluate_send(
        session, "Sure, let's grab dinner sometime!",
        confidence="high", is_first_outbound=True,
        trigger_text="Can you confirm the invoice amount for the consulting project?",
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert any("no keywords" in r for r in decision.reasons)


def test_operator_first_reply_with_keyword_overlap_passes():
    session = _session(voice=models.VOICE_OPERATOR, trust_level=3)
    decision = gate.evaluate_send(
        session, "The consulting invoice amount is confirmed at the quoted rate.",
        confidence="high", is_first_outbound=True,
        trigger_text="Can you confirm the invoice amount for the consulting project?",
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


def test_operator_non_first_reply_skips_relevance_check():
    session = _session(voice=models.VOICE_OPERATOR, trust_level=3)
    decision = gate.evaluate_send(
        session, "Sure, let's grab dinner sometime!",
        confidence="high", is_first_outbound=False,
        trigger_text="Can you confirm the invoice amount for the consulting project?",
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_AUTO_SEND


# ---------------------------------------------------------------------------
# Trust-level routing — L1 / L2 / L3
# ---------------------------------------------------------------------------


def test_trust_1_always_holds_regardless_of_confidence():
    session = _session(trust_level=1)
    for conf in ("high", "medium", "low"):
        decision = gate.evaluate_send(
            session, "Sounds good!", confidence=conf, is_first_outbound=False,
            sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
        )
        assert decision.decision == gate.DECISION_HOLD
        assert decision.hard_floor_violated is False
        assert any("trust_level=1" in r for r in decision.reasons)


def test_trust_2_auto_sends_only_on_high_confidence():
    session = _session(trust_level=2)
    high = gate.evaluate_send(
        session, "Sounds good!", confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert high.decision == gate.DECISION_AUTO_SEND

    for conf in ("medium", "low"):
        decision = gate.evaluate_send(
            session, "Sounds good!", confidence=conf, is_first_outbound=False,
            sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
        )
        assert decision.decision == gate.DECISION_HOLD
        assert decision.hard_floor_violated is False
        assert any("trust_level=2" in r for r in decision.reasons)


def test_trust_3_auto_sends_regardless_of_confidence_when_hard_floor_clean():
    session = _session(trust_level=3)
    for conf in ("high", "medium", "low", None):
        decision = gate.evaluate_send(
            session, "Sounds good!", confidence=conf, is_first_outbound=False,
            sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
        )
        assert decision.decision == gate.DECISION_AUTO_SEND


def test_hard_floor_beats_trust_3():
    """The one invariant that must never break: trust=3 is 'autonomous',
    not 'unconditional' — PLAN.md §6.2 is explicit that L3 auto-sends
    'unless hard floor trips'."""
    session = _session(trust_level=3)
    decision = gate.evaluate_send(
        session, "I'll go ahead and pay the deposit for you.",
        confidence="high", is_first_outbound=False,
        sent_last_hour=0, hard_floor_cfg=HARD_FLOOR,
    )
    assert decision.decision == gate.DECISION_HOLD
    assert decision.hard_floor_violated is True


# ---------------------------------------------------------------------------
# disclosure_present — the mechanical check itself
# ---------------------------------------------------------------------------


def test_disclosure_present_matches_required_phrasing():
    assert gate.disclosure_present("I'm your AI assistant, here to help.")
    assert gate.disclosure_present("As an AI, I can't promise that.")
    assert not gate.disclosure_present("Hey there, just checking in!")
    assert not gate.disclosure_present("")
