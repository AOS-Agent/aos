"""Draft feedback handler.

Processes operator responses to draft replies:
- /reply_accept_{id} — Send the draft as-is
- /reply_edit_{id}   — Prompt for edited version, send that instead
- /reply_discard_{id} — Drop it

Every action writes to surface_feedback — the graduation engine reads
this to decide when to promote/demote trust levels.

Also handles style learning: when the operator edits a draft, the
diff is saved so future drafts learn from corrections.
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
STYLE_EDITS_DIR = Path.home() / ".aos" / "work" / "comms" / "style_edits"


def _load_pending() -> dict:
    if PENDING_DRAFTS.exists():
        try:
            return json.loads(PENDING_DRAFTS.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_pending(drafts: dict):
    PENDING_DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    PENDING_DRAFTS.write_text(json.dumps(drafts, indent=2, default=str))


def _write_feedback(person_id: str, surface_type: str, operator_action: str,
                    original: str = "", final: str = "", session_id: str = ""):
    """Write a surface_feedback row to the People DB."""
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        conn = people_db.connect()

        import random, string
        fid = "sf_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))

        conn.execute(
            "INSERT INTO surface_feedback "
            "(id, person_id, surface_type, surface_at, operator_action, action_at, "
            " original_content, final_content, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fid, person_id, surface_type, int(time.time()), operator_action,
             int(time.time()), original, final, session_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to write feedback: {e}")


def _save_style_edit(person_id: str, original: str, final: str):
    """Save a style correction for future learning."""
    STYLE_EDITS_DIR.mkdir(parents=True, exist_ok=True)
    edit_file = STYLE_EDITS_DIR / f"{person_id}.jsonl"
    entry = json.dumps({
        "person_id": person_id,
        "original": original,
        "final": final,
        "ts": time.time(),
    })
    with open(edit_file, "a") as f:
        f.write(entry + "\n")


def handle_accept(draft_id: str) -> dict | None:
    """Accept a draft — send it as-is.

    Returns the draft record for the bridge to send, or None if not found.
    """
    pending = _load_pending()
    draft = pending.pop(draft_id, None)
    if not draft:
        return None

    # Log feedback
    _write_feedback(
        person_id=draft["person_id"],
        surface_type="draft",
        operator_action="accepted",
        original=draft["draft_text"],
        final=draft["draft_text"],
    )

    _save_pending(pending)
    log.info(f"Draft accepted for {draft['person_name']}")
    return draft


def handle_edit(draft_id: str, edited_text: str) -> dict | None:
    """Edit a draft — send the edited version instead.

    Returns the draft record (with updated text) for the bridge to send.
    """
    pending = _load_pending()
    draft = pending.pop(draft_id, None)
    if not draft:
        return None

    original = draft["draft_text"]

    # Log feedback with the correction
    _write_feedback(
        person_id=draft["person_id"],
        surface_type="draft",
        operator_action="edited",
        original=original,
        final=edited_text,
    )

    # Save style edit for learning
    _save_style_edit(draft["person_id"], original, edited_text)

    # Update draft with edited text
    draft["draft_text"] = edited_text
    draft["was_edited"] = True

    _save_pending(pending)
    log.info(f"Draft edited for {draft['person_name']}: \"{edited_text[:60]}...\"")
    return draft


def handle_discard(draft_id: str) -> dict | None:
    """Discard a draft — don't send anything.

    Returns the draft record for logging, or None if not found.
    """
    pending = _load_pending()
    draft = pending.pop(draft_id, None)
    if not draft:
        return None

    # Log feedback
    _write_feedback(
        person_id=draft["person_id"],
        surface_type="draft",
        operator_action="dismissed",
        original=draft["draft_text"],
    )

    _save_pending(pending)
    log.info(f"Draft discarded for {draft['person_name']}")
    return draft


def get_pending_draft(draft_id: str) -> dict | None:
    """Get a pending draft by ID without removing it."""
    pending = _load_pending()
    return pending.get(draft_id)


def list_pending() -> list[dict]:
    """List all pending drafts."""
    pending = _load_pending()
    return list(pending.values())
