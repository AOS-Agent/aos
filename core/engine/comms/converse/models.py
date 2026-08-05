"""Typed models for the converse engine.

Backs the three tables in ~/.aos/data/comms.db defined by schema.sql:
conversation_sessions, session_messages, session_actions. See
~/.aos/tmp/sessions-build/PLAN.md §2 for the full design — this module is
the SINGLE SOURCE of the status/state enums referenced there; db.py, the
turn handler (converse/turn.py, T2b), and the Qareen API (core/qareen/api/
converse.py, T2c) all import from here instead of re-declaring string
literals. sqlite has no enum type, so these are plain string constants
plus grouping tuples for validation and status-set queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# conversation_sessions enums
# ---------------------------------------------------------------------------

MODE_SENTINEL = "sentinel"
MODE_ENVOY = "envoy"
MODES = (MODE_SENTINEL, MODE_ENVOY)

VOICE_OPERATOR = "operator"
VOICE_AGENT = "agent"
VOICES = (VOICE_OPERATOR, VOICE_AGENT)

CHANNEL_IMESSAGE = "imessage"
CHANNEL_SLACK = "slack"
CHANNELS = (CHANNEL_IMESSAGE, CHANNEL_SLACK)

TOOLS_NONE = "none"
TOOLS_RESEARCH = "research"
TOOLS_FULL = "full"
TOOLS_PROFILES = (TOOLS_NONE, TOOLS_RESEARCH, TOOLS_FULL)

# Session status (PLAN.md §2 "Session status enum")
STATUS_ACTIVE = "active"        # watching for inbound; idle between turns
STATUS_HANDLING = "handling"    # a claude -p turn is in flight
STATUS_WAITING = "waiting"      # agent chose 'wait'; still watching
STATUS_ESCALATED = "escalated"  # agent asked for the operator; auto-handling suspended
STATUS_TAKEOVER = "takeover"    # operator drives manually; auto-handling suspended
STATUS_PAUSED = "paused"        # operator paused (or channel reauth needed)
STATUS_COMPLETE = "complete"    # terminal
STATUS_STOPPED = "stopped"      # terminal
STATUS_EXPIRED = "expired"      # terminal
STATUS_CAPPED = "capped"        # terminal
STATUS_FAILED = "failed"        # terminal

SESSION_STATUSES = (
    STATUS_ACTIVE, STATUS_HANDLING, STATUS_WAITING, STATUS_ESCALATED,
    STATUS_TAKEOVER, STATUS_PAUSED,
    STATUS_COMPLETE, STATUS_STOPPED, STATUS_EXPIRED, STATUS_CAPPED, STATUS_FAILED,
)
TERMINAL_STATUSES = (STATUS_COMPLETE, STATUS_STOPPED, STATUS_EXPIRED, STATUS_CAPPED, STATUS_FAILED)
ACTIVE_STATUSES = tuple(s for s in SESSION_STATUSES if s not in TERMINAL_STATUSES)

PAUSED_REASON_OPERATOR = "operator"
PAUSED_REASON_ESCALATED = "escalated"
PAUSED_REASON_CAPPED = "capped"
PAUSED_REASON_REAUTH = "reauth"
PAUSED_REASON_EXPIRED = "expired"
PAUSED_REASONS = (
    PAUSED_REASON_OPERATOR, PAUSED_REASON_ESCALATED, PAUSED_REASON_CAPPED,
    PAUSED_REASON_REAUTH, PAUSED_REASON_EXPIRED,
)

CLOSE_REASONS = (STATUS_COMPLETE, STATUS_STOPPED, STATUS_EXPIRED, STATUS_CAPPED, STATUS_FAILED)

# ---------------------------------------------------------------------------
# session_messages enums
# ---------------------------------------------------------------------------

ROLE_CONTACT = "contact"
ROLE_AGENT = "agent"
ROLE_OPERATOR = "operator"
ROLE_SYSTEM = "system"
ROLES = (ROLE_CONTACT, ROLE_AGENT, ROLE_OPERATOR, ROLE_SYSTEM)

DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"
DIRECTION_INTERNAL = "internal"
DIRECTIONS = (DIRECTION_INBOUND, DIRECTION_OUTBOUND, DIRECTION_INTERNAL)

# inbound lifecycle
MSG_RECEIVED = "received"
MSG_HANDLING = "handling"
MSG_HANDLED = "handled"
MSG_FAILED = "failed"
INBOUND_STATES = (MSG_RECEIVED, MSG_HANDLING, MSG_HANDLED, MSG_FAILED)

# outbound lifecycle
MSG_QUEUED = "queued"
MSG_SENT = "sent"
MSG_SEND_FAILED = "send_failed"
OUTBOUND_STATES = (MSG_QUEUED, MSG_SENT, MSG_SEND_FAILED)

# internal (operator notes / system events)
MSG_DONE = "done"
INTERNAL_STATES = (MSG_DONE,)

MESSAGE_STATES = INBOUND_STATES + OUTBOUND_STATES + INTERNAL_STATES

# ---------------------------------------------------------------------------
# session_actions enums
# ---------------------------------------------------------------------------

ACTION_SEND_REPLY = "send_reply"
ACTION_HUMAN_TOUCHPOINT = "human_touchpoint"
ACTION_CLOSE = "close"
ACTION_KINDS = (ACTION_SEND_REPLY, ACTION_HUMAN_TOUCHPOINT, ACTION_CLOSE)

ACTION_PROPOSED = "proposed"
ACTION_APPROVED = "approved"
ACTION_REJECTED = "rejected"
ACTION_EXECUTED = "executed"
ACTION_EXPIRED = "expired"
ACTION_STATUSES = (ACTION_PROPOSED, ACTION_APPROVED, ACTION_REJECTED, ACTION_EXECUTED, ACTION_EXPIRED)

# Handler turn output "action" verbs (PLAN.md §5 output contract) — distinct
# from the session status enum above, this is what a claude -p turn returns.
TURN_REPLY = "reply"
TURN_WAIT = "wait"
TURN_COMPLETE = "complete"
TURN_ESCALATE = "escalate"
TURN_ACTIONS = (TURN_REPLY, TURN_WAIT, TURN_COMPLETE, TURN_ESCALATE)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ConversationSession:
    id: str
    mode: str
    voice: str
    channel: str
    conversation_ref: str
    counterpart_handle: str
    mission: str
    status: str
    created_at: int
    updated_at: int
    person_id: str | None = None
    person_name: str | None = None
    success_criteria: str | None = None
    constraints: str | None = None
    tools: str = TOOLS_NONE
    trust_level: int = 2
    paused_reason: str | None = None
    cursor: str | None = None
    state_summary: str | None = None
    artifacts: str | None = None
    handling_started_at: int | None = None
    turn_count: int = 0
    sent_count: int = 0
    error_count: int = 0
    max_messages: int = 30
    expires_at: int | None = None
    origin: str | None = None
    closed_at: int | None = None
    close_reason: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ConversationSession":
        d = dict(row)
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass
class SessionMessage:
    id: str
    session_id: str
    role: str
    direction: str
    text: str
    state: str
    ts: str
    created_at: int
    channel_message_id: str | None = None
    attempt_count: int = 0
    error: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "SessionMessage":
        d = dict(row)
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SessionAction:
    id: str
    session_id: str
    kind: str
    payload: str
    status: str
    created_at: int
    gate_reasons: str | None = None
    decided_at: int | None = None
    executed_at: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "SessionAction":
        d = dict(row)
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)
