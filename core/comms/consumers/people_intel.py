"""People Intelligence consumer.

Receives unified messages from the message bus and writes interaction
records to the People Intelligence database. This is the first consumer
and the proof that the bus → consumer pattern works.

For each batch of messages:
1. Group messages by conversation + sender
2. Resolve each sender to a person via their identifier (phone/email)
3. Write one interaction per person per conversation per batch
4. Skip messages from unresolved senders (log, don't crash)
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)

# People Intelligence DB — lives at ~/.aos/data/
_PEOPLE_SERVICE = Path.home() / ".aos" / "data"


def _get_people_db():
    """Lazy import of the People Intelligence DB module."""
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        return people_db
    except ImportError:
        log.warning("People Intelligence DB not available at %s", _PEOPLE_SERVICE)
        return None


def _get_resolver():
    """Lazy import of the contact resolver from core/comms/."""
    try:
        from .. import resolver
        return resolver
    except ImportError:
        # Fallback: try instance path
        if str(_PEOPLE_SERVICE) not in sys.path:
            sys.path.insert(0, str(_PEOPLE_SERVICE))
        try:
            import resolver
            return resolver
        except ImportError:
            log.debug("Contact resolver not available — falling back to basic lookup")
            return None


class PeopleIntelConsumer(Consumer):
    """Writes message interactions to the People Intelligence database."""

    name = "people_intel"

    def __init__(self):
        self._db_mod = None
        self._conn = None
        self._resolver = None

    @property
    def db(self):
        """Lazy-load the people DB module."""
        if self._db_mod is None:
            self._db_mod = _get_people_db()
        return self._db_mod

    @property
    def conn(self):
        """Lazy connection to the people DB."""
        if self._conn is None and self.db:
            self._conn = self.db.connect()
        return self._conn

    def process(self, messages: list[Message]) -> int:
        """Process a batch of messages into interaction records.

        Groups messages by (conversation, sender) and creates one
        interaction record per group. Returns count of interactions created.
        """
        if not messages or not self.db or not self.conn:
            return 0

        # Split inbound and outbound
        inbound: list[Message] = []
        outbound: list[Message] = []
        for msg in messages:
            if msg.from_me:
                outbound.append(msg)
            else:
                inbound.append(msg)

        # Group inbound: (conversation_id, sender) -> list of messages
        groups: dict[tuple[str, str], list[Message]] = defaultdict(list)
        for msg in inbound:
            key = (msg.conversation_id, msg.sender)
            groups[key].append(msg)

        # Group outbound: (conversation_id) -> list of messages
        # For outbound, the "person" is the recipient (conversation_id)
        for msg in outbound:
            key = (msg.conversation_id, msg.conversation_id)
            groups[key].append(msg)

        interactions_created = 0

        for (conv_id, sender), msgs in groups.items():
            # Resolve sender to a person
            person = self._resolve_sender(sender, msgs[0].channel)
            if not person:
                log.debug(
                    "Unresolved sender '%s' on %s (%d msgs) — skipping",
                    sender, msgs[0].channel, len(msgs)
                )
                continue

            # Check if we already have a recent interaction for this person + channel
            # (avoid duplicates on repeated polls)
            earliest = min(m.timestamp for m in msgs)
            latest = max(m.timestamp for m in msgs)

            if self._interaction_exists(person["id"], msgs[0].channel, earliest):
                continue

            # Determine direction
            direction = "outbound" if msgs[0].from_me else "inbound"

            # Create interaction record
            interaction_id = self.db._nanoid("ix")
            now = self.db.now_ts()

            try:
                self.conn.execute("""
                    INSERT INTO interactions
                        (id, person_id, occurred_at, channel, direction, msg_count, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    interaction_id,
                    person["id"],
                    int(earliest.timestamp()),
                    msgs[0].channel,
                    direction,
                    len(msgs),
                    now,
                ))
                self.conn.commit()
                interactions_created += 1
                log.debug(
                    "Logged interaction %s: %s on %s (%d msgs)",
                    interaction_id, person["canonical_name"],
                    msgs[0].channel, len(msgs)
                )
            except Exception as e:
                log.error("Failed to log interaction for %s: %s", person["id"], e)

        return interactions_created

    def _resolve_sender(self, sender: str, channel: str) -> dict | None:
        """Resolve a message sender to a person in the People DB.

        Uses the full 5-tier resolver (aliases, exact, frequency, phonetic, fuzzy)
        when available. Falls back to basic identifier lookup otherwise.
        """
        if not sender or sender == "me":
            return None

        # Try the full resolver first (5-tier pipeline)
        if self._resolver is None:
            self._resolver = _get_resolver() or False  # False = tried and failed

        if self._resolver and self._resolver is not False:
            try:
                result = self._resolver.resolve_contact(sender, context=channel, conn=self.conn)
                if result and result.get("resolved") and result.get("person_id"):
                    # Return the person dict in the format the consumer expects
                    person = self.db.get_person(self.conn, result["person_id"])
                    if person:
                        return person
            except Exception as e:
                log.debug("Resolver error for '%s': %s — falling back to basic lookup", sender, e)

        # Fallback: basic identifier lookup (original logic)
        conn = self.conn

        # Try as phone number
        normalized_phone = sender.replace(" ", "").replace("-", "")
        if normalized_phone.startswith("+") or normalized_phone.isdigit():
            if not normalized_phone.startswith("+"):
                normalized_phone = "+" + normalized_phone
            person = self.db.find_person_by_identifier(conn, "phone", normalized_phone)
            if person:
                return person

        # Try as email
        if "@" in sender:
            person = self.db.find_person_by_identifier(conn, "email", sender.lower())
            if person:
                return person

        # Try as WhatsApp JID
        if channel == "whatsapp":
            person = self.db.find_person_by_identifier(conn, "wa_jid", sender)
            if person:
                return person

        # Fallback: name search
        matches = self.db.find_person_by_name(conn, sender)
        if len(matches) == 1:
            return matches[0]

        return None

    def _interaction_exists(self, person_id: str, channel: str, earliest: datetime) -> bool:
        """Check if an interaction already exists for this person/channel/time."""
        ts = int(earliest.timestamp())
        # Window: within 60 seconds of the earliest message
        row = self.conn.execute("""
            SELECT 1 FROM interactions
            WHERE person_id = ? AND channel = ? AND ABS(occurred_at - ?) < 60
            LIMIT 1
        """, (person_id, channel, ts)).fetchone()
        return row is not None

    def on_error(self, error: Exception, message: Message | None = None) -> None:
        log.error("PeopleIntelConsumer error: %s", error, exc_info=True)
