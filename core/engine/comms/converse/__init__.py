"""Converse — the Conversation-Session engine.

Turns the hand-built Sana loop + Sentinel + Envoy into one first-class AOS
primitive: a supervised runtime that holds goal-directed multi-turn
conversations with real people over iMessage and Slack, one fresh
`claude -p` reasoning session per inbound turn, fully visible and
controllable in Qareen. Full design: ~/.aos/tmp/sessions-build/PLAN.md.

Sentinel (mode='sentinel', voice='operator') and Envoy (mode='envoy',
voice='agent') are the two *modes* of this one runtime, not separate
systems — see PLAN.md §1. This package currently ships only the foundation
(Wave 0 / T1): the schema, typed models, and CRUD layer. The channel
adapters, handler/turn layer, supervisor daemon, and Qareen API land in
later waves per PLAN.md §9.

    models.py   — dataclasses + the single-source status/state enums
    db.py       — typed CRUD over conversation_sessions, session_messages,
                  session_actions (comms.db). Everything else imports this.
    schema.sql  — the DDL, applied by db.connect() and by migration 100.
"""

from . import db, models

__all__ = ["db", "models"]
