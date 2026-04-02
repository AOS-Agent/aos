"""Comms Orchestrator — single bus consumer for the full message lifecycle.

Receives unified messages from all channels and routes each through
the trust cascade:

  L3 (ACT)     → autonomous handler (auto-reply if eligible, else fall to L2)
  L2 (DRAFT)   → draft engine (generate + present on Telegram for approval)
  L1 (SURFACE) → triage notification (alert operator about important messages)
  L0 (OBSERVE) → log interaction only (learn patterns silently)

Also runs the pattern updater and people intel logging inline.
One consumer, one cascade, everything connected.

Registered in the MessageBus alongside (or replacing) individual consumers.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .bus import Consumer
from .models import Message

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
TRUST_PATH = Path.home() / ".aos" / "config" / "trust.yaml"


def _get_people_db():
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        return people_db
    except ImportError:
        return None


def _get_resolver():
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import resolver
        return resolver
    except ImportError:
        return None


def _load_trust_config() -> dict:
    try:
        import yaml
        with open(TRUST_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_person_level(person_id: str, trust_config: dict) -> int:
    per_person = trust_config.get("comms", {}).get("per_person", {})
    entry = per_person.get(person_id, {})
    if isinstance(entry, dict):
        return entry.get("level", 0)
    return 0


class CommsOrchestrator(Consumer):
    """Single consumer that handles the full comms lifecycle."""

    name = "comms_orchestrator"

    def process(self, messages: list[Message]) -> int:
        """Process a batch of messages through the trust cascade.

        Returns number of messages that triggered an action (draft/auto/surface).
        """
        if not messages:
            return 0

        people_db = _get_people_db()
        resolver_mod = _get_resolver()
        if not people_db or not resolver_mod:
            return 0

        conn = people_db.connect()
        trust_config = _load_trust_config()

        actions = 0
        resolved_cache: dict[str, dict] = {}

        # Group inbound messages by sender for efficiency
        inbound = [m for m in messages if not m.from_me]

        for msg in inbound:
            try:
                # ── Resolve sender ──────────────────────────
                sender_key = msg.sender
                if sender_key not in resolved_cache:
                    # Try identifier-based resolution first (phone/email)
                    result = resolver_mod.resolve_contact(sender_key, conn=conn)
                    resolved_cache[sender_key] = result

                resolution = resolved_cache[sender_key]
                if not resolution.get("resolved") or not resolution.get("person_id"):
                    continue

                person_id = resolution["person_id"]
                person_name = resolution.get("contact", {}).get("name", sender_key)

                # ── Log interaction ─────────────────────────
                _log_interaction(conn, people_db, person_id, msg)

                # ── Trust cascade ───────────────────────────
                level = _get_person_level(person_id, trust_config)

                if level >= 3:
                    # L3: Try autonomous handling
                    handled = _try_autonomous(
                        person_id, person_name, msg, trust_config
                    )
                    if handled:
                        actions += 1
                        continue
                    # Fall through to L2

                if level >= 2:
                    # L2: Generate draft
                    drafted = _try_draft(
                        person_id, person_name, msg, conn
                    )
                    if drafted:
                        actions += 1
                        continue
                    # Fall through to L1

                if level >= 1:
                    # L1: Surface notification (triage handles this)
                    # The triage consumer already runs separately —
                    # we just log that surfacing was appropriate
                    actions += 1
                    continue

                # L0: Observe only — interaction already logged above

            except Exception as e:
                log.debug(f"Orchestrator error for {msg.sender}: {e}")
                continue

        conn.commit()
        conn.close()

        if actions > 0:
            log.info(f"Orchestrator: {actions} actions from {len(inbound)} inbound messages")

        return actions


def _log_interaction(conn, people_db, person_id: str, msg: Message):
    """Log an interaction row for this message."""
    import random
    import string

    ts = int(msg.timestamp.timestamp())

    # Deduplicate — don't log if we already have one within 60s
    existing = conn.execute(
        "SELECT 1 FROM interactions WHERE person_id = ? AND channel = ? "
        "AND ABS(occurred_at - ?) < 60",
        (person_id, msg.channel, ts),
    ).fetchone()

    if existing:
        return

    ix_id = "ix_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    direction = "outbound" if msg.from_me else "inbound"

    conn.execute(
        "INSERT OR IGNORE INTO interactions "
        "(id, person_id, occurred_at, channel, direction, msg_count, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (ix_id, person_id, ts, msg.channel, direction, people_db.now_ts()),
    )


def _try_autonomous(
    person_id: str,
    person_name: str,
    msg: Message,
    trust_config: dict,
) -> bool:
    """Try to handle a message autonomously (Level 3).

    Returns True if handled, False to fall through to drafting.
    """
    try:
        _aos_dev = str(Path.home() / "project" / "aos")
        _aos_root = str(Path.home() / "aos")
        for p in [_aos_dev, _aos_root]:
            if p not in sys.path:
                sys.path.insert(0, p)

        from core.comms.autonomous.handler import handle_autonomous
        result = handle_autonomous(
            person_id=person_id,
            person_name=person_name,
            channel=msg.channel,
            conversation_id=msg.conversation_id,
            inbound_text=msg.text,
        )
        return result is not None
    except Exception as e:
        log.debug(f"Autonomous handling failed: {e}")
        return False


def _try_draft(
    person_id: str,
    person_name: str,
    msg: Message,
    conn,
) -> bool:
    """Try to generate a draft reply (Level 2).

    Returns True if draft was generated and sent to operator.
    """
    try:
        _aos_dev = str(Path.home() / "project" / "aos")
        _aos_root = str(Path.home() / "aos")
        for p in [_aos_dev, _aos_root]:
            if p not in sys.path:
                sys.path.insert(0, p)

        from core.comms.drafts.consumer import process_message_for_drafting
        result = process_message_for_drafting(
            person_id=person_id,
            person_name=person_name,
            channel=msg.channel,
            conversation_id=msg.conversation_id,
            inbound_text=msg.text,
        )
        return result is not None
    except Exception as e:
        log.debug(f"Draft generation failed: {e}")
        return False
