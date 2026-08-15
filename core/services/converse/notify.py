"""Converse — operator notifications (PLAN.md §4/§6: "Escalation always
flows to the operator ... never to the third party").

Thin wrapper around the shared Telegram router (core/engine/notify/router.py
— the same one the `aos-notify` CLI uses) so the supervisor never talks to
Telegram directly. Every event the supervisor can raise (reauth needed,
escalated after repeated failures, agent asked for the operator, session
completed/expired/capped, a reply held for approval, sent, send failed) has
one line here. Never raises — a notification failure must not take down the
supervisor loop; it is logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_send_notification = None  # lazily resolved, cached; False = known-unavailable


def _router():
    global _send_notification
    if _send_notification is None:
        try:
            from core.engine.notify.router import send_notification

            _send_notification = send_notification
        except Exception as e:  # pragma: no cover - environment without the router
            log.warning("converse notify: router unavailable (%s) — logging only", e)
            _send_notification = False
    return _send_notification


# event -> (kind, message template). kind picks the Telegram topic:
# "alert" routes to the alerts topic, everything else to system.
EVENT_TEMPLATES: dict[str, tuple[str, str]] = {
    "reauth_needed": (
        "alert",
        "Converse: {channel} needs reauth ({error}). Run `converse reauth {channel}`.",
    ),
    "escalated_after_failures": (
        "alert",
        "Converse session {session_id} ({person}) escalated after 3 failed turns: {error}",
    ),
    "escalate": (
        "alert",
        "Converse session {session_id} ({person}) asked for you: {summary}",
    ),
    "complete": (
        "info",
        "Converse session {session_id} ({person}) completed: {summary}",
    ),
    "expired": (
        "info",
        "Converse session {session_id} ({person}) expired.",
    ),
    "capped": (
        "info",
        "Converse session {session_id} ({person}) hit its message cap ({sent_count}/{max_messages}).",
    ),
    "held_for_approval": (
        "info",
        "Converse session {session_id} ({person}) has a reply awaiting your approval: {reasons}",
    ),
    "sent": (
        "info",
        "Converse session {session_id} ({person}) sent a reply.",
    ),
    "send_failed": (
        "alert",
        "Converse session {session_id} ({person}) failed to send: {error}",
    ),
    "new_inbound_while_escalated": (
        "info",
        "Converse session {session_id} ({person}) has new messages while escalated.",
    ),
}


def notify(event: str, *, enabled: bool = True, **fields: Any) -> bool:
    """Fire a converse operator notification for `event`. `enabled` lets a
    caller pass a config on_* flag straight through without branching at
    every call site (e.g. `notify("sent", enabled=cfg["notify"]["on_send"],
    ...)`). Unknown fields referenced by a template default to sane
    placeholders rather than raising a KeyError. Never raises.
    """
    if not enabled:
        return False
    template = EVENT_TEMPLATES.get(event)
    if template is None:
        log.warning("converse notify: unknown event %r", event)
        return False
    kind, fmt = template

    safe_fields = {
        "person": fields.get("person") or fields.get("person_name") or fields.get("counterpart_handle") or "contact",
        "error": fields.get("error") or "unknown error",
        "summary": fields.get("summary") or "",
        "reasons": "; ".join(fields.get("reasons") or []) or "gate held it",
        "sent_count": fields.get("sent_count", "?"),
        "max_messages": fields.get("max_messages", "?"),
        **fields,
    }
    try:
        text = fmt.format(**safe_fields)
    except Exception:
        text = f"Converse: {event} ({fields})"

    fn = _router()
    if not fn:
        log.info("converse notify (no router configured): [%s] %s", kind, text)
        return False
    try:
        result = fn(text, topic="alerts" if kind == "alert" else "system", kind=kind)
        return bool(result.get("delivered"))
    except Exception as e:  # noqa: BLE001 — notification must never crash the caller
        log.warning("converse notify: send failed for event=%r: %s", event, e)
        return False
