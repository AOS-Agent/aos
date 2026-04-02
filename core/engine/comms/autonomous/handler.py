"""Autonomous message handler + circuit breaker + digest.

Handles the full Level 3 flow:
1. Check eligibility (hard guardrails)
2. Generate draft (must be >= 0.85 confidence)
3. Send automatically
4. Log to autonomous_log + surface_feedback
5. Check circuit breaker (instant demotion if too many corrections)

The daily digest reads the autonomous_log and summarizes what was sent.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
AUTONOMOUS_LOG = Path.home() / ".aos" / "work" / "comms" / "autonomous_log.jsonl"
TRUST_PATH = Path.home() / ".aos" / "config" / "trust.yaml"
MIN_CONFIDENCE = 0.85


def _load_trust_config() -> dict:
    try:
        import yaml
        with open(TRUST_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_trust_config(config: dict):
    import yaml
    with open(TRUST_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def _log_autonomous_action(person_id: str, person_name: str, channel: str,
                           message_excerpt: str, reply_excerpt: str):
    """Append to autonomous action log."""
    AUTONOMOUS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": time.time(),
        "person_id": person_id,
        "person_name": person_name,
        "channel": channel,
        "message_excerpt": message_excerpt[:100],
        "reply_excerpt": reply_excerpt[:100],
    })
    with open(AUTONOMOUS_LOG, "a") as f:
        f.write(entry + "\n")


def _write_feedback(person_id: str, operator_action: str, original: str, final: str = ""):
    """Write autonomous feedback to surface_feedback table."""
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        conn = people_db.connect()
        import random
        import string
        fid = "sf_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        conn.execute(
            "INSERT INTO surface_feedback "
            "(id, person_id, surface_type, surface_at, operator_action, action_at, "
            " original_content, final_content) "
            "VALUES (?, ?, 'autonomous', ?, ?, ?, ?, ?)",
            (fid, person_id, int(time.time()), operator_action,
             int(time.time()), original, final or original),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to write autonomous feedback: {e}")


# ── Circuit Breaker ──────────────────────────────────────

def check_circuit_breaker(person_id: str) -> bool:
    """Check if this person should be demoted from Level 3.

    Reads last 5 autonomous surface_feedback entries.
    If 2+ are dismissed or edited, instantly demote to Level 2.

    Returns True if demotion was triggered.
    """
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))
    try:
        import db as people_db
        conn = people_db.connect()

        recent = conn.execute(
            "SELECT operator_action FROM surface_feedback "
            "WHERE person_id = ? AND surface_type = 'autonomous' "
            "ORDER BY surface_at DESC LIMIT 5",
            (person_id,),
        ).fetchall()

        conn.close()

        if len(recent) < 2:
            return False

        bad_count = sum(1 for r in recent if r["operator_action"] in ("dismissed", "edited"))
        if bad_count >= 2:
            # Instant demotion
            config = _load_trust_config()
            per_person = config.setdefault("comms", {}).setdefault("per_person", {})
            per_person[person_id] = {"level": 2, "updated_at": time.time()}
            _save_trust_config(config)

            # Audit
            audit_path = Path.home() / ".aos" / "logs" / "comms-graduation.log"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps({
                "ts": time.time(), "person_id": person_id,
                "from_level": 3, "to_level": 2,
                "reason": f"Circuit breaker: {bad_count}/5 recent autonomous actions corrected",
            })
            with open(audit_path, "a") as f:
                f.write(entry + "\n")

            log.warning(f"Circuit breaker triggered for {person_id}: demoted 3→2")
            return True

    except Exception as e:
        log.error(f"Circuit breaker check failed: {e}")

    return False


# ── Autonomous Handler ───────────────────────────────────

def handle_autonomous(
    person_id: str,
    person_name: str,
    channel: str,
    conversation_id: str,
    inbound_text: str,
    recent_messages: list[dict] | None = None,
) -> dict | None:
    """Handle a message autonomously if eligible.

    Flow:
    1. Check eligibility
    2. Generate draft (must be >= 0.85 confidence)
    3. Send via adapter
    4. Log everything
    5. Check circuit breaker

    Returns action dict if handled, None if fell back to draft.
    """
    from .eligibility import is_auto_eligible

    # Get trust level
    config = _load_trust_config()
    per_person = config.get("comms", {}).get("per_person", {})
    entry = per_person.get(person_id, {})
    level = entry.get("level", 0) if isinstance(entry, dict) else 0

    # Check eligibility
    eligibility = is_auto_eligible(inbound_text, person_id, level)
    if not eligibility.eligible:
        log.debug(f"Not auto-eligible for {person_name}: {eligibility.reason}")
        return None

    # Generate draft
    if str(_PEOPLE_SERVICE) not in sys.path:
        sys.path.insert(0, str(_PEOPLE_SERVICE))

    try:
        import db as people_db
        conn = people_db.connect()

        from ..drafts.context import assemble_context
        from ..drafts.drafter import draft_reply

        ctx = assemble_context(
            person_id=person_id,
            conversation_id=conversation_id,
            channel=channel,
            conn=conn,
            last_inbound=inbound_text,
            recent_messages=recent_messages,
        )
        result = draft_reply(ctx)
        conn.close()

        # Confidence gate
        if result.confidence < MIN_CONFIDENCE:
            log.info(f"Confidence too low for auto ({result.confidence:.0%}), falling back to draft")
            return None

        if not result.text:
            return None

        # TODO: Send via adapter (bus.send)
        # For now, log the action — actual send wiring happens when
        # the bus.send() integration is connected
        _log_autonomous_action(person_id, person_name, channel,
                               inbound_text, result.text)
        _write_feedback(person_id, "autonomous", result.text)

        # Check circuit breaker after action
        check_circuit_breaker(person_id)

        return {
            "person_id": person_id,
            "person_name": person_name,
            "channel": channel,
            "inbound": inbound_text,
            "reply": result.text,
            "confidence": result.confidence,
            "template_key": eligibility.template_key,
        }

    except Exception as e:
        log.error(f"Autonomous handling failed for {person_name}: {e}")
        return None


# ── Daily Digest ─────────────────────────────────────────

def get_daily_digest() -> str:
    """Generate a digest of autonomous actions from the last 24 hours.

    Returns formatted text for the morning briefing.
    """
    if not AUTONOMOUS_LOG.exists():
        return ""

    cutoff = time.time() - 86400
    actions = []

    try:
        for line in AUTONOMOUS_LOG.read_text().strip().split("\n"):
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("ts", 0) >= cutoff:
                actions.append(entry)
    except (json.JSONDecodeError, OSError):
        return ""

    if not actions:
        return ""

    lines = [f"🤖 <b>Autonomous Comms</b> — {len(actions)} messages handled"]
    for a in actions[-10:]:  # Last 10
        lines.append(
            f"  → {a.get('person_name', '?')} ({a.get('channel', '?')}): "
            f"\"{a.get('reply_excerpt', '')}\""
        )

    return "\n".join(lines)
