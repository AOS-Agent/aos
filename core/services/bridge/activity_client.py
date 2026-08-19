"""Bridge activity hooks — no-op seam since Qareen was decommissioned (aos#208).

These functions used to POST activity/conversation rows to the Qareen
dashboard (:4096 → ingest_activity / ingest_conversations tables). Nothing
ever read those rows, so the transport and the tables were removed with the
rest of Qareen.

The call sites across the bridge (telegram_channel, slack_channel, heartbeat)
are kept: they mark exactly the moments a future activity feed (aos-app)
will want — message received, response sent, agent invoked, job completed.
Wire the new backend in here; the signatures are the contract.
"""

from __future__ import annotations

from typing import Any, Optional


def log_activity(agent: str, action: str, parent_agent: Optional[str] = None,
                 **kwargs: Any) -> None:
    """No-op. Returns None (callers treat the id as optional)."""
    return None


def update_activity(activity_id: Any, status: str, summary: Optional[str] = None,
                    **kwargs: Any) -> None:
    """No-op."""
    return None


def log_conversation(user_key: str, agent: Optional[str] = None,
                     topic_name: Optional[str] = None, **kwargs: Any) -> None:
    """No-op. Returns None (callers treat the conversation id as optional)."""
    return None


def update_conversation(conv_id: Any, response: str,
                        duration_ms: Optional[int] = None) -> None:
    """No-op."""
    return None
