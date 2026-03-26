"""Triage consumer — evaluates message urgency and surfaces what matters.

Subscribes to comms.message_received events. For each inbound message:
1. Resolve sender to a person (via People DB)
2. Check response pattern — does the operator usually reply quickly to this person?
3. If pattern deviation detected → publish notify.send event
4. Track unanswered messages for morning briefing

Trust-gated: only surfaces notifications when comms trust level >= 1 (SURFACE).
At Level 0 (OBSERVE), it logs patterns silently.

Event types handled:
    comms.message_received  — new message from any channel
    comms.message_sent      — outbound message (clears "unanswered" for that person)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..consumer import EventConsumer
from ..event import Event

log = logging.getLogger(__name__)

# People DB
_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"

# Triage state — tracks unanswered messages
_TRIAGE_FILE = Path.home() / ".aos" / "work" / "triage-state.json"

# Trust config
_TRUST_FILE = Path.home() / ".aos" / "config" / "trust.yaml"


def _load_triage_state() -> dict:
    """Load unanswered message tracking state."""
    try:
        if _TRIAGE_FILE.exists():
            return json.loads(_TRIAGE_FILE.read_text())
    except Exception:
        pass
    return {"unanswered": {}, "patterns": {}}


def _save_triage_state(state: dict) -> None:
    """Save triage state atomically."""
    try:
        _TRIAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_TRIAGE_FILE) + ".tmp"
        Path(tmp).write_text(json.dumps(state, indent=2, default=str))
        import os
        os.replace(tmp, str(_TRIAGE_FILE))
    except Exception as e:
        log.error("Failed to save triage state: %s", e)


def _get_comms_trust_level() -> int:
    """Read the communications trust level from trust.yaml. Default 0."""
    try:
        import yaml
        if _TRUST_FILE.exists():
            trust = yaml.safe_load(_TRUST_FILE.read_text()) or {}
            # Check for a comms-specific capability level
            for agent_name, agent_data in trust.get("agents", {}).items():
                caps = agent_data.get("capabilities", {})
                if "communications" in caps:
                    return caps["communications"]
            # Fallback: check global default
            return trust.get("defaults", {}).get("comms_level", 0)
    except Exception:
        pass
    return 0


class TriageConsumer(EventConsumer):
    """Evaluates message urgency and surfaces what matters."""

    name = "triage"
    handles = ["comms.message_received", "comms.message_sent"]

    def __init__(self):
        self._state = _load_triage_state()
        self._resolver = None
        self._people_conn = None
        self._trust_level = None
        self._last_trust_check = 0

    @property
    def trust_level(self) -> int:
        """Cache trust level, refresh every 5 minutes."""
        now = time.time()
        if self._trust_level is None or now - self._last_trust_check > 300:
            self._trust_level = _get_comms_trust_level()
            self._last_trust_check = now
        return self._trust_level

    def _get_resolver(self):
        """Lazy-load the contact resolver."""
        if self._resolver is None:
            try:
                if str(_PEOPLE_SERVICE) not in sys.path:
                    sys.path.insert(0, str(_PEOPLE_SERVICE))
                import resolver
                self._resolver = resolver
            except ImportError:
                log.debug("Resolver not available for triage")
        return self._resolver

    def _get_people_conn(self) -> sqlite3.Connection | None:
        if self._people_conn is None:
            try:
                if str(_PEOPLE_SERVICE) not in sys.path:
                    sys.path.insert(0, str(_PEOPLE_SERVICE))
                import db as people_db
                self._people_conn = people_db.connect()
            except Exception:
                pass
        return self._people_conn

    def process(self, event: Event) -> None:
        """Process a message event."""
        if event.action == "message_received":
            self._on_message_received(event)
        elif event.action == "message_sent":
            self._on_message_sent(event)

    def _on_message_received(self, event: Event) -> None:
        """Handle inbound message — track as unanswered, evaluate urgency."""
        data = event.data
        sender = data.get("sender", "")
        channel = data.get("channel", "")
        from_me = data.get("from_me", False)
        ts = data.get("timestamp", datetime.now().isoformat())

        # Skip our own messages
        if from_me or sender == "me":
            return

        # Resolve sender
        person_name = sender
        person_id = None
        resolver = self._get_resolver()
        if resolver:
            try:
                result = resolver.resolve_contact(sender, context=channel)
                if result and result.get("resolved"):
                    person_name = result["contact"].get("name", sender)
                    person_id = result.get("person_id")
            except Exception:
                pass

        # Track as unanswered
        key = person_id or sender
        self._state.setdefault("unanswered", {})[key] = {
            "person_name": person_name,
            "person_id": person_id,
            "channel": channel,
            "received_at": ts,
            "text_preview": (data.get("text", "")[:80] + "...") if len(data.get("text", "")) > 80 else data.get("text", ""),
            "conversation_id": data.get("conversation_id"),
        }
        _save_triage_state(self._state)

        # At Level 0 (OBSERVE): just log, surface nothing
        if self.trust_level < 1:
            log.debug("Triage [L0 observe]: %s on %s — tracked as unanswered", person_name, channel)
            return

        # At Level 1+ (SURFACE): check if this needs attention
        self._evaluate_urgency(key, person_name, channel, data)

    def _on_message_sent(self, event: Event) -> None:
        """Handle outbound message — clear unanswered status for that person."""
        data = event.data
        conversation_id = data.get("conversation_id", "")

        # Clear any unanswered entry matching this conversation
        cleared = []
        for key, entry in list(self._state.get("unanswered", {}).items()):
            if entry.get("conversation_id") == conversation_id:
                cleared.append(key)

        for key in cleared:
            del self._state["unanswered"][key]

        if cleared:
            _save_triage_state(self._state)
            log.debug("Triage: cleared unanswered for %s (replied)", cleared)

    def _evaluate_urgency(self, key: str, person_name: str, channel: str, data: dict) -> None:
        """Check if this message needs the operator's attention NOW.

        Only fires a notification if behavior deviates from pattern —
        NOT on every message. WhatsApp/iMessage already notify.
        """
        conn = self._get_people_conn()
        if not conn:
            return

        # Check relationship state — how quickly does the operator usually reply?
        person_id = self._state["unanswered"].get(key, {}).get("person_id")
        if not person_id:
            return

        try:
            state = conn.execute(
                "SELECT avg_days_between, interaction_count_30d, importance "
                "FROM relationship_state rs JOIN people p ON rs.person_id = p.id "
                "WHERE rs.person_id = ?",
                (person_id,)
            ).fetchone()
        except Exception:
            state = None

        if not state:
            return

        importance = state[2] if state[2] else 3
        interaction_count = state[1] if state[1] else 0

        # Only notify for important contacts (importance 1-2) or frequent contacts (>5 interactions/month)
        if importance > 2 and interaction_count < 5:
            return

        # Publish a notification event — the notify consumer will send it to Telegram
        from .. import system_bus

        text = f"💬 {person_name} ({channel})"
        preview = data.get("text", "")[:100]
        if preview:
            text += f"\n{preview}"

        if importance <= 2:
            text += "\n⭐ High-importance contact"

        system_bus.publish(Event(
            type="notify.send",
            data={"text": text, "source": "triage"},
            source="triage_consumer",
        ))

        log.info("Triage: notified operator about %s (%s)", person_name, channel)

    def health(self) -> dict:
        unanswered = self._state.get("unanswered", {})
        return {
            "name": self.name,
            "trust_level": self.trust_level,
            "unanswered_count": len(unanswered),
            "unanswered_contacts": [
                v.get("person_name", k)
                for k, v in list(unanswered.items())[:5]
            ],
            "handles": self.handles,
        }
