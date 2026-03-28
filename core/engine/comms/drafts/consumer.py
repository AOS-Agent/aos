"""Draft consumer — generates reply drafts for Level 2+ contacts.

Sits in the message bus pipeline. When inbound messages arrive from
people whose trust level >= 2, it assembles context, generates a draft,
and sends it to Telegram for operator review.

The consumer writes pending drafts to a JSON file that the bridge's
feedback handler reads. This decouples the bus poll cycle from the
Telegram interaction.

Flow:
  Bus poll → new inbound message from Level 2+ person
    → assemble_context()
    → draft_reply()
    → save to pending_drafts.json
    → notify operator via Telegram
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
PENDING_DRAFTS = Path.home() / ".aos" / "work" / "comms" / "pending_drafts.json"
TRUST_PATH = Path.home() / ".aos" / "config" / "trust.yaml"


def _load_trust_config() -> dict:
    try:
        import yaml
        with open(TRUST_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_person_level(person_id: str) -> int:
    """Get current comms trust level for a person."""
    config = _load_trust_config()
    per_person = config.get("comms", {}).get("per_person", {})
    entry = per_person.get(person_id, {})
    if isinstance(entry, dict):
        return entry.get("level", 0)
    return 0


def _load_pending() -> dict:
    """Load pending drafts. Keyed by draft_id."""
    if PENDING_DRAFTS.exists():
        try:
            return json.loads(PENDING_DRAFTS.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_pending(drafts: dict):
    PENDING_DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    PENDING_DRAFTS.write_text(json.dumps(drafts, indent=2, default=str))


def _notify_draft(person_name: str, channel: str, draft_text: str, draft_id: str):
    """Send draft notification to Telegram."""
    try:
        import subprocess
        script = Path.home() / "aos" / "core" / "lib" / "notify.py"
        if script.exists():
            msg = (
                f"📝 <b>Draft reply to {person_name}</b> ({channel})\n\n"
                f"<i>{draft_text}</i>\n\n"
                f"/reply_accept_{draft_id} — Send as-is\n"
                f"/reply_edit_{draft_id} — Edit and send\n"
                f"/reply_discard_{draft_id} — Discard"
            )
            subprocess.run(
                [sys.executable, str(script), msg],
                capture_output=True, timeout=10,
            )
    except Exception as e:
        log.debug(f"Failed to notify draft: {e}")


def _nanoid(prefix: str = "d") -> str:
    import random, string
    return f"{prefix}_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def process_message_for_drafting(
    person_id: str,
    person_name: str,
    channel: str,
    conversation_id: str,
    inbound_text: str,
    recent_messages: list[dict] | None = None,
) -> dict | None:
    """Check if a person qualifies for drafting, and if so, generate a draft.

    Called by the bus consumer for each inbound message.
    Returns the draft result dict, or None if not eligible.
    """
    # Check trust level
    level = _get_person_level(person_id)
    if level < 2:
        return None

    log.info(f"Drafting reply for {person_name} (Level {level})...")

    # Assemble context
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))

    try:
        import db as people_db
        conn = people_db.connect()

        from .context import assemble_context
        ctx = assemble_context(
            person_id=person_id,
            conversation_id=conversation_id,
            channel=channel,
            conn=conn,
            last_inbound=inbound_text,
            recent_messages=recent_messages,
        )

        # Generate draft
        from .drafter import draft_reply
        result = draft_reply(ctx)

        conn.close()

        if not result.text:
            log.info(f"  Draft empty for {person_name}: {result.warning}")
            return None

        # Save pending draft
        draft_id = _nanoid()
        draft_record = {
            "id": draft_id,
            "person_id": person_id,
            "person_name": person_name,
            "channel": channel,
            "conversation_id": conversation_id,
            "inbound_text": inbound_text,
            "draft_text": result.text,
            "confidence": result.confidence,
            "warning": result.warning,
            "created_at": time.time(),
        }

        pending = _load_pending()
        pending[draft_id] = draft_record
        _save_pending(pending)

        # Notify operator
        _notify_draft(person_name, channel, result.text, draft_id)

        log.info(f"  Draft sent for {person_name}: \"{result.text[:60]}...\" (conf={result.confidence:.0%})")
        return draft_record

    except Exception as e:
        log.error(f"Draft generation failed for {person_name}: {e}")
        return None
