"""intelligence.hooks — Event-driven automation rules.

Reads action rules from ~/.aos/config/feeds.yaml and runs matching
actions when intelligence events fire. Currently supports:

    - `intelligence.brief_created` — fired by the ingest runner per new item
    - `intelligence.brief_compiled` — fired by the save endpoint after compile
    - `intelligence.proposal_pending` — fired when a proposal lands in review

Each event can trigger: Telegram notification, EventBus broadcast, task
creation. All actions are best-effort — failures are logged but don't
bubble up to the caller.
"""

from .actions import (
    ActionRule,
    emit_brief_created,
    emit_brief_compiled,
    emit_proposal_pending,
    load_action_rules,
)

__all__ = [
    "ActionRule",
    "emit_brief_created",
    "emit_brief_compiled",
    "emit_proposal_pending",
    "load_action_rules",
]
