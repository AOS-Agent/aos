"""Converse — the safety gate (Wave 1 / T2b). PLAN.md §6.

Every outbound message a turn produces (a 'reply', or the optional holding
message on 'complete'/'escalate') MUST pass through evaluate_send() before
a channel's send() is ever called. This module decides ONLY that — it does
not send, does not touch session_actions/session_messages, and does not
run the handler. The caller (the supervisor, core/services/converse, T3)
is expected to:

  1. call turn.run_turn() to get a ParsedTurn
  2. if it produced a message, call gate.evaluate_send() here
  3. on GateDecision.auto_send: call channel.send(), then db.mark_message_sent
  4. otherwise: db.propose_action(session_id, ACTION_SEND_REPLY, {...},
     gate_reasons=decision.reasons) — this is the "send_reply proposals
     from a held/gated reply are inserted by converse/gate.py, not
     [apply_turn_result]" boundary called out in db.py's own docstring.

Reuses, not reinvents (PLAN.md §6 preamble): the blocked-intent-word list
and the trigger/reply keyword-overlap check are sentinel/confidence_gate.py's
own logic (`DEFAULT_BLOCKED_WORDS`, `_significant_tokens`) — converse layers
its own trust-level routing (L1/L2/L3), per-session rate limiting, and the
voice=agent disclosure check on top, rather than re-deriving any of that
from scratch weaker than sentinel already has it.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import models
except ImportError:  # loaded standalone (tests, migrations)
    import models  # type: ignore

# sentinel/confidence_gate.py — reused for DEFAULT_BLOCKED_WORDS and the
# significant-token overlap helper. Imported defensively: converse code may
# run from either ~/aos or ~/project/aos, and sentinel is a sibling package
# under core/engine/comms/, not a converse submodule.
try:
    from ..sentinel import confidence_gate as sentinel_gate
except ImportError:  # pragma: no cover - standalone/test import fallback
    import sys

    _sentinel_dir = Path(__file__).resolve().parents[1] / "sentinel"
    if str(_sentinel_dir) not in sys.path:
        sys.path.insert(0, str(_sentinel_dir))
    import confidence_gate as sentinel_gate  # type: ignore

CONFIG_PATH = Path.home() / ".aos" / "config" / "converse.yaml"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

# Matches prompts.DISCLOSURE_PHRASE ("AI assistant") plus a couple of
# equivalent phrasings, so a handler that follows the persona instructions
# (which literally require the words "AI assistant") always passes this.
# This is a deliberately narrow, literal check, not free-form NLU — PLAN.md
# §6.3 calls the disclosure requirement "mechanically verified", and a
# mechanical check is only trustworthy if the phrasing it looks for is the
# same phrasing the prompt requires the model to use.
DISCLOSURE_PHRASES = (
    "ai assistant",
    "an ai",
    "i'm an ai",
    "i am an ai",
)

DEFAULT_HARD_FLOOR = {
    "blocked_intent_words": list(sentinel_gate.DEFAULT_BLOCKED_WORDS),
    "block_inner_circle_importance": 1,
    "max_sends_per_hour": 6,
}

DECISION_AUTO_SEND = "auto_send"
DECISION_HOLD = "hold"


@dataclass
class GateDecision:
    decision: str  # DECISION_AUTO_SEND | DECISION_HOLD
    reasons: list[str] = field(default_factory=list)
    hard_floor_violated: bool = False

    @property
    def auto_send(self) -> bool:
        return self.decision == DECISION_AUTO_SEND


# ---------------------------------------------------------------------------
# Config / lookups
# ---------------------------------------------------------------------------


def load_hard_floor_config(config_path: Path | str | None = None) -> dict:
    """Read `hard_floor:` from converse.yaml, falling back to
    DEFAULT_HARD_FLOOR (which is itself sourced from sentinel's own
    defaults) for any missing key. A missing/unparseable config file never
    disables the hard floor — it just uses defaults."""
    path = Path(config_path) if config_path else CONFIG_PATH
    merged = dict(DEFAULT_HARD_FLOOR)
    if not path.exists():
        return merged
    try:
        import yaml

        cfg = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return merged
    hf = cfg.get("hard_floor") or {}
    if isinstance(hf, dict):
        merged.update(hf)
    return merged


def contact_importance(person_id: str | None, *, people_db_path: Path | str | None = None) -> int | None:
    """People-DB importance (1=inner circle .. 4=peripheral); None if
    unresolved. Defaults to 3 (matching sentinel/context_builder.py's
    ContactProfile default) only if the person IS found but the column is
    NULL — an unresolved person_id returns None so the caller can decide
    how to treat "unknown" (currently: never treated as inner-circle)."""
    if not person_id:
        return None
    path = Path(people_db_path) if people_db_path else PEOPLE_DB
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT importance FROM people WHERE id = ?", (person_id,)).fetchone()
        if row is None:
            return None
        return int(row[0]) if row[0] is not None else 3
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def sent_count_last_hour(session_id: str, *, db_path: Path | str | None = None) -> int:
    """Outbound messages this session actually sent in the last hour —
    the per-session rate limit input (PLAN.md §6.1 max_sends_per_hour).
    Counts only state='sent' rows (queued-but-not-yet-sent don't count
    against the limit; a held/gated message that never sends shouldn't
    block a later one)."""
    path = Path(db_path) if db_path else COMMS_DB
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        cutoff = int(time.time()) - 3600
        row = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ? AND role = ? "
            "AND direction = ? AND state = ? AND created_at >= ?",
            (session_id, models.ROLE_AGENT, models.DIRECTION_OUTBOUND, models.MSG_SENT, cutoff),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def has_sent_before(session_id: str, *, db_path: Path | str | None = None) -> bool:
    """Whether this session has ever successfully sent an agent message —
    the is_first_outbound input for the voice=agent disclosure check and
    the voice=operator relevance check (both PLAN.md §6.3 "on the first
    reply" / "message 1")."""
    path = Path(db_path) if db_path else COMMS_DB
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT 1 FROM session_messages WHERE session_id = ? AND role = ? "
            "AND direction = ? AND state = ? LIMIT 1",
            (session_id, models.ROLE_AGENT, models.DIRECTION_OUTBOUND, models.MSG_SENT),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def disclosure_present(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in DISCLOSURE_PHRASES)


# ---------------------------------------------------------------------------
# Hard floor
# ---------------------------------------------------------------------------


def _check_hard_floor(
    message: str,
    *,
    session: "models.ConversationSession",
    contact_importance_value: int | None,
    sent_last_hour: int,
    hard_floor_cfg: dict,
) -> list[str]:
    reasons: list[str] = []

    if not (message or "").strip():
        reasons.append("HARD FLOOR: empty message body")
        return reasons  # nothing else meaningful to check on an empty body

    blocked = [str(w).lower() for w in hard_floor_cfg.get("blocked_intent_words", [])]
    body_lower = message.lower()
    for w in blocked:
        if w and re.search(rf"\b{re.escape(w)}\b", body_lower):
            reasons.append(f"HARD FLOOR: message contains blocked intent word '{w}'")
            break

    if session.voice == models.VOICE_OPERATOR:
        block_imp = int(hard_floor_cfg.get("block_inner_circle_importance", 1))
        if contact_importance_value is not None and contact_importance_value == block_imp:
            reasons.append("HARD FLOOR: inner-circle contact (voice=operator auto-send always held)")

    max_per_hour = int(hard_floor_cfg.get("max_sends_per_hour", 6))
    if sent_last_hour >= max_per_hour:
        reasons.append(f"HARD FLOOR: rate limit ({sent_last_hour}/{max_per_hour} sends in the last hour)")

    if session.sent_count >= session.max_messages:
        reasons.append(f"HARD FLOOR: session at max_messages cap ({session.sent_count}/{session.max_messages})")

    if session.expires_at is not None and session.expires_at < int(time.time()):
        reasons.append("HARD FLOOR: session has expired")

    return reasons


def _check_voice_rules(
    message: str,
    *,
    session: "models.ConversationSession",
    is_first_outbound: bool,
    trigger_text: str | None,
) -> list[str]:
    """PLAN.md §6.3 voice rules — treated as hard-floor-strength (never
    auto-sendable regardless of trust level), same as sentinel treats its
    own relevance check as a hard floor."""
    reasons: list[str] = []

    if session.voice == models.VOICE_AGENT and is_first_outbound:
        if not disclosure_present(message):
            reasons.append(
                "HARD FLOOR: voice=agent first message is missing the required AI disclosure"
            )

    if session.voice == models.VOICE_OPERATOR and is_first_outbound and trigger_text:
        trg_tokens = sentinel_gate._significant_tokens(trigger_text)
        if trg_tokens:
            msg_tokens = sentinel_gate._significant_tokens(message)
            if not (trg_tokens & msg_tokens):
                reasons.append(
                    "HARD FLOOR: first reply shares no keywords with the mission/trigger text "
                    f"(trigger tokens={sorted(trg_tokens)[:6]})"
                )

    return reasons


# ---------------------------------------------------------------------------
# Trust-level routing
# ---------------------------------------------------------------------------


def evaluate_send(
    session: "models.ConversationSession",
    message: str,
    *,
    confidence: str | None,
    is_first_outbound: bool,
    contact_importance_value: int | None = None,
    trigger_text: str | None = None,
    sent_last_hour: int | None = None,
    hard_floor_cfg: dict | None = None,
    db_path: Path | str | None = None,
) -> GateDecision:
    """Decide auto_send vs hold for one outbound message (PLAN.md §6.2).

    Hard floor + voice rules run first and are trust-level-independent —
    any violation forces `hold` no matter what trust_level says (L3
    autonomous included: "auto-send unless hard floor trips"). Only once
    the message is hard-floor-clean does trust_level get to decide:

      L1 (<=1): every reply held for approval, always.
      L2 (==2): auto-send only if confidence=='high'; else held.
      L3 (>=3): auto-send.

    `hold` never means "dropped" — PLAN.md §6 and sentinel/spawner.py's
    precedent (hard-floor-blocked drafts still land in the pending queue,
    just tagged) both route held messages to session_actions for the
    operator, never silently discard them. The caller creates that
    session_actions row (see module docstring) — this function only
    decides.
    """
    cfg = hard_floor_cfg or load_hard_floor_config()

    if sent_last_hour is None:
        sent_last_hour = sent_count_last_hour(session.id, db_path=db_path)

    reasons = _check_hard_floor(
        message,
        session=session,
        contact_importance_value=contact_importance_value,
        sent_last_hour=sent_last_hour,
        hard_floor_cfg=cfg,
    )
    reasons += _check_voice_rules(
        message,
        session=session,
        is_first_outbound=is_first_outbound,
        trigger_text=trigger_text,
    )

    if reasons:
        return GateDecision(decision=DECISION_HOLD, reasons=reasons, hard_floor_violated=True)

    trust = session.trust_level
    if trust <= 1:
        return GateDecision(
            decision=DECISION_HOLD,
            reasons=["trust_level=1: every reply requires operator approval"],
        )
    if trust >= 3:
        return GateDecision(decision=DECISION_AUTO_SEND, reasons=[])

    # trust == 2: confidence-gated
    if (confidence or "").lower() == "high":
        return GateDecision(decision=DECISION_AUTO_SEND, reasons=[])
    return GateDecision(
        decision=DECISION_HOLD,
        reasons=[f"trust_level=2: confidence={confidence!r} (need 'high' to auto-send)"],
    )
