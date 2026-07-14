"""Build the context bundle handed to Sentinel.

A bundle contains everything she needs to make a confident draft:
- The trigger message + last N messages in this thread (both directions)
- Contact profile from people.db (name, importance, relationship)
- Voice samples: operator's last N messages to THIS contact
- A short instruction header
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
IMESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"

DEFAULT_CONVO_DEPTH = 30
DEFAULT_VOICE_SAMPLES = 15


@dataclass
class ContactProfile:
    person_id: Optional[str]
    canonical_name: str
    first_name: Optional[str]
    importance: int
    relationship: Optional[str]
    is_inner_circle: bool
    handle: Optional[str] = None       # raw recipient handle (phone/email) — used by dispatcher


@dataclass
class ConversationMessage:
    direction: str           # inbound/outbound
    text: str
    timestamp: str           # ISO


@dataclass
class ContextBundle:
    trigger_id: str
    trigger_message_id: str
    trigger_phrase: str
    channel: str
    contact: ContactProfile
    conversation: list[ConversationMessage]
    voice_samples: list[str]
    draft_path: str
    trigger_text: str = ""        # Full text of the message that triggered Sentinel
    trigger_timestamp: str = ""   # ISO timestamp of the trigger message

    def to_text(self) -> str:
        """Render as the prompt Sentinel receives."""
        c = self.contact
        lines = []
        lines.append(f"You are Sentinel. The operator just used trigger phrase '{self.trigger_phrase}' in an iMessage to {c.canonical_name}.")
        lines.append("")
        # === TRIGGER MESSAGE — MUST be the first concrete content Sentinel sees ===
        lines.append("=== MOST RECENT MESSAGE FROM OPERATOR (this is what just triggered you) ===")
        lines.append(f"[{self.trigger_timestamp}] OPERATOR: {self.trigger_text}")
        lines.append("=== END TRIGGER MESSAGE ===")
        lines.append("")
        lines.append(
            ">>> THE TASK MUST BE INFERRED FROM THE TRIGGER MESSAGE ABOVE. "
            "The conversation history below is for tone and continuity ONLY. "
            "Do NOT pick up a task from older messages. If the trigger message "
            "is ambiguous or unrelated to prior context, mark confidence low "
            "and in_scope false — do not invent a task."
        )
        lines.append("")
        lines.append("=== CONTACT ===")
        lines.append(f"Name: {c.canonical_name}")
        if c.first_name:
            lines.append(f"First name: {c.first_name}")
        lines.append(f"Importance: {c.importance} (1=inner circle, 2=active, 3=acquaintance, 4=peripheral)")
        if c.relationship:
            lines.append(f"Relationship: {c.relationship}")
        if c.is_inner_circle:
            lines.append("⚠ INNER CIRCLE — extra care required. If confidence isn't crystal clear, mark low.")
        lines.append("")
        lines.append("=== CONVERSATION (most recent last) ===")
        for m in self.conversation:
            who = "OPERATOR" if m.direction == "outbound" else c.canonical_name.upper()
            lines.append(f"[{m.timestamp}] {who}: {m.text}")
        lines.append("")
        if self.voice_samples:
            lines.append("=== OPERATOR'S VOICE WITH THIS CONTACT (last messages, for tone matching) ===")
            for v in self.voice_samples:
                lines.append(f"• {v}")
            lines.append("")
        lines.append("=== INSTRUCTIONS ===")
        lines.append(f"1. Infer the task from the TRIGGER MESSAGE above (NOT from older conversation history).")
        lines.append(f"2. Decide if it's in scope (research only — no bookings/payments/scheduling).")
        lines.append(f"3. If in scope, research it. Cite sources. Be specific.")
        lines.append(f"4. Draft the reply message in the operator's voice with this contact.")
        lines.append(f"5. Self-check before writing: does my draft directly respond to the operator's TRIGGER MESSAGE? If I'm answering a question they didn't ask, mark confidence low.")
        lines.append(f"6. Write the draft file to: {self.draft_path}")
        lines.append(f"   (Frontmatter exactly as defined in your system prompt.)")
        lines.append(f"7. Output ONE line on stdout: SENTINEL_DRAFT_READY: {self.draft_path}")
        lines.append("")
        lines.append(f"trigger_id: {self.trigger_id}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ContextBuilder:
    """Assembles a ContextBundle from comms.db + people.db."""

    def __init__(self, comms_db_path: Path = COMMS_DB, people_db_path: Path = PEOPLE_DB):
        self.comms_db_path = comms_db_path
        self.people_db_path = people_db_path

    def build(self, trigger_id: str,
              convo_depth: int = DEFAULT_CONVO_DEPTH,
              voice_samples: int = DEFAULT_VOICE_SAMPLES) -> ContextBundle:
        """Build a bundle for a specific trigger row."""
        trg = self._load_trigger(trigger_id)
        if not trg:
            raise ValueError(f"Trigger {trigger_id} not found")

        # Get the trigger message to know the conversation.
        # ALWAYS prefer chat.db for the trigger text — comms.db may lag the
        # poll cycle and miss the just-sent message even when the conversation
        # itself is known. We fall through to comms.db only if chat.db has no row.
        trg_msg = self._load_message_from_chatdb(trg["message_id"])
        if not trg_msg:
            trg_msg = self._load_message(trg["message_id"])
        if not trg_msg:
            raise ValueError(f"Message {trg['message_id']} not found in chat.db or comms.db")

        # Build contact profile (resolves outbound via chat.db when needed)
        contact = self._build_contact_profile(
            trg["person_id"], trg_msg["sender_id"],
            conversation_id=trg_msg["conversation_id"],
            channel=trg["channel"],
        )

        # Pull conversation history (last N messages in same conversation)
        conversation = self._pull_conversation(
            trg_msg["conversation_id"], trg_msg["timestamp"], convo_depth,
            channel=trg["channel"],
        )

        # Always make sure the trigger message itself is the last entry.
        # If comms.db hasn't caught up yet, the trigger may be missing.
        trigger_text = (trg_msg.get("content") or "").strip()
        trigger_ts = trg_msg.get("timestamp") or ""
        trigger_id_str = trg_msg.get("id") or trg["message_id"]
        if not any(
            (m.text or "").strip() == trigger_text
            and m.timestamp == trigger_ts
            for m in conversation
        ):
            conversation.append(ConversationMessage(
                direction="outbound",
                text=trigger_text,
                timestamp=trigger_ts,
            ))

        # Pull voice samples (operator's last N outbound msgs to this person)
        voice = self._pull_voice_samples(trg["person_id"], trg_msg["conversation_id"], voice_samples)

        draft_path = str(Path.home() / ".aos" / "work" / "sentinel" / "drafts" / f"{trigger_id}.md")

        return ContextBundle(
            trigger_id=trigger_id,
            trigger_message_id=trg["message_id"],
            trigger_phrase=trg["trigger_phrase"],
            channel=trg["channel"],
            contact=contact,
            conversation=conversation,
            voice_samples=voice,
            draft_path=draft_path,
            trigger_text=trigger_text,
            trigger_timestamp=trigger_ts,
        )

    # ── Internal ────────────────────────────────────────────────────

    def _comms_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.comms_db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _people_conn(self) -> Optional[sqlite3.Connection]:
        if not self.people_db_path.exists():
            return None
        conn = sqlite3.connect(str(self.people_db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _load_trigger(self, trigger_id: str) -> Optional[dict]:
        with self._comms_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
            return dict(row) if row else None

    def _load_message(self, msg_id: str) -> Optional[dict]:
        with self._comms_conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (msg_id,)
            ).fetchone()
            return dict(row) if row else None

    def _load_message_from_chatdb(self, msg_id: str) -> Optional[dict]:
        """Fall back to reading the trigger message from iMessage chat.db directly.

        Used when comms.db hasn't caught up yet (race between watcher and
        comms-bus poll). The message_id format is 'im-<rowid>' for iMessage.
        Returns a dict shaped like a comms.db row.
        """
        if not msg_id or not msg_id.startswith("im-"):
            return None
        try:
            rowid = int(msg_id[3:])
        except ValueError:
            return None
        chat_db = Path.home() / "Library" / "Messages" / "chat.db"
        if not chat_db.exists():
            return None
        try:
            uri = f"file:{chat_db}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2)
            row = conn.execute("""
                SELECT m.rowid, m.text, m.attributedBody, m.date, m.is_from_me,
                       c.rowid AS chat_rowid
                FROM message m
                LEFT JOIN chat_message_join cmj ON m.rowid = cmj.message_id
                LEFT JOIN chat c ON cmj.chat_id = c.rowid
                WHERE m.rowid = ? LIMIT 1
            """, (rowid,)).fetchone()
            conn.close()
            if not row:
                return None
            # Decode text from attributedBody if text column is empty
            content = row[1]
            if not content:
                from .attributedbody import extract_text
                content = extract_text(row[2]) or ""
            # Apple ns timestamp → ISO string
            from datetime import datetime
            APPLE_EPOCH = 978307200
            ts = datetime.fromtimestamp(row[3] / 1_000_000_000 + APPLE_EPOCH).isoformat()
            return {
                "id": msg_id,
                "channel": "imessage",
                "direction": "outbound" if row[4] else "inbound",
                "sender_id": "me" if row[4] else "",
                "content": content,
                "timestamp": ts,
                "conversation_id": str(row[5]) if row[5] is not None else None,
                "person_id": None,
            }
        except Exception as e:
            log.warning("chat.db fallback failed for %s: %s", msg_id, e)
            return None

    def _build_contact_profile(self, person_id: Optional[str],
                                sender_id: Optional[str],
                                conversation_id: Optional[str] = None,
                                channel: str = "imessage") -> ContactProfile:
        """Pull profile from people.db, resolving outbound msgs via chat.db when needed."""
        # If the trigger came from an outbound message, sender_id will be 'me'.
        # We need the OTHER participant — look up via chat.db using conversation_id.
        handle = None
        if channel == "imessage" and (sender_id in (None, "", "me") or not person_id):
            handle = self._resolve_imessage_recipient(conversation_id)
            if handle:
                # Try to resolve handle → person via people.db
                person_id = person_id or self._lookup_person_by_identifier(handle)

        canonical = handle or sender_id or "Unknown"
        first = None
        importance = 3
        relationship = None

        pconn = self._people_conn()
        if pconn and person_id:
            try:
                row = pconn.execute(
                    "SELECT canonical_name, first_name, importance FROM people WHERE id = ?",
                    (person_id,)
                ).fetchone()
                if row:
                    canonical = row["canonical_name"] or canonical
                    first = row["first_name"]
                    importance = row["importance"] or 3
                # Relationships (optional table — best-effort)
                try:
                    rel = pconn.execute("""
                        SELECT relationship_type FROM relationships
                        WHERE person_id = ? LIMIT 1
                    """, (person_id,)).fetchone()
                    if rel:
                        relationship = rel["relationship_type"]
                except sqlite3.OperationalError:
                    pass
            except sqlite3.OperationalError:
                pass
            finally:
                pconn.close()

        return ContactProfile(
            person_id=person_id,
            canonical_name=canonical,
            first_name=first,
            importance=importance,
            relationship=relationship,
            is_inner_circle=(importance == 1),
            handle=handle,
        )

    def _resolve_imessage_recipient(self, conversation_id: Optional[str]) -> Optional[str]:
        """Map comms.db conversation_id to the OTHER participant's handle via chat.db.

        comms.db stores conversation_id as the iMessage chat rowid (e.g., '11').
        We join chat → chat_handle_join → handle to find the recipient.
        """
        if not conversation_id or not IMESSAGE_DB.exists():
            return None
        try:
            chat_rowid = int(conversation_id)
        except (TypeError, ValueError):
            return None

        # Open chat.db read-only with immutable URI (no copy, no lock contention)
        uri = f"file:{IMESSAGE_DB}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2)
            row = conn.execute("""
                SELECT h.id FROM chat_handle_join chj
                JOIN handle h ON chj.handle_id = h.ROWID
                WHERE chj.chat_id = ?
                LIMIT 1
            """, (chat_rowid,)).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def _lookup_person_by_identifier(self, identifier: str) -> Optional[str]:
        """Look up a person_id by raw handle (phone/email) in people.db."""
        pconn = self._people_conn()
        if not pconn:
            return None
        try:
            # Try common identifier types
            row = pconn.execute("""
                SELECT person_id FROM person_identifiers
                WHERE identifier_value = ? OR identifier_value = ?
                LIMIT 1
            """, (identifier, identifier.lower())).fetchone()
            return row["person_id"] if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            pconn.close()

    def _pull_conversation(self, conv_id: Optional[str], up_to_ts: str,
                            limit: int, channel: str = "imessage") -> list[ConversationMessage]:
        if not conv_id:
            return []
        with self._comms_conn() as conn:
            rows = conn.execute("""
                SELECT direction, content, timestamp
                FROM messages
                WHERE conversation_id = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (conv_id, up_to_ts, limit)).fetchall()
        # If comms.db has no record of this conversation (e.g., the bus hasn't
        # caught up yet), fall back to chat.db for iMessage.
        if not rows and channel == "imessage":
            return self._pull_conversation_from_chatdb(conv_id, up_to_ts, limit)
        # Reverse to chronological
        rows = list(reversed(rows))
        return [
            ConversationMessage(
                direction=r["direction"],
                text=(r["content"] or ""),
                timestamp=r["timestamp"],
            )
            for r in rows
        ]

    def _pull_conversation_from_chatdb(self, conv_id: str, up_to_ts: str,
                                        limit: int) -> list[ConversationMessage]:
        """Chat.db fallback when comms.db has no record of this conversation.

        conv_id is the chat.db chat rowid (string). Decodes attributedBody for
        messages whose text column is NULL.
        """
        try:
            chat_rowid = int(conv_id)
        except (TypeError, ValueError):
            return []
        if not IMESSAGE_DB.exists():
            return []
        try:
            uri = f"file:{IMESSAGE_DB}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2)
            rows = conn.execute("""
                SELECT m.rowid, m.text, m.attributedBody, m.date, m.is_from_me
                FROM message m
                JOIN chat_message_join cmj ON m.rowid = cmj.message_id
                WHERE cmj.chat_id = ?
                ORDER BY m.rowid DESC
                LIMIT ?
            """, (chat_rowid, limit)).fetchall()
            conn.close()
        except Exception as e:
            log.warning("chat.db conv fallback failed for %s: %s", conv_id, e)
            return []

        from .attributedbody import extract_text
        from datetime import datetime
        APPLE_EPOCH = 978307200

        out: list[ConversationMessage] = []
        for rowid, text, ab, date, is_from_me in rows:
            content = text
            if not content:
                content = extract_text(ab) or ""
            if not content.strip():
                continue
            try:
                ts = datetime.fromtimestamp(date / 1_000_000_000 + APPLE_EPOCH).isoformat()
            except Exception:
                ts = ""
            out.append(ConversationMessage(
                direction="outbound" if is_from_me else "inbound",
                text=content,
                timestamp=ts,
            ))
        # Reverse to chronological order (oldest first)
        return list(reversed(out))

    def _pull_voice_samples(self, person_id: Optional[str],
                            conv_id: Optional[str], limit: int) -> list[str]:
        """Operator's recent outbound messages to this contact."""
        with self._comms_conn() as conn:
            if person_id:
                rows = conn.execute("""
                    SELECT content FROM messages
                    WHERE person_id = ? AND direction = 'outbound'
                      AND content != '' AND content IS NOT NULL
                    ORDER BY timestamp DESC LIMIT ?
                """, (person_id, limit)).fetchall()
            elif conv_id:
                rows = conn.execute("""
                    SELECT content FROM messages
                    WHERE conversation_id = ? AND direction = 'outbound'
                      AND content != '' AND content IS NOT NULL
                    ORDER BY timestamp DESC LIMIT ?
                """, (conv_id, limit)).fetchall()
            else:
                rows = []
        return [r["content"] for r in rows if r["content"]]
