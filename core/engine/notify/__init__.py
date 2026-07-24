"""Topic-aware Telegram notification routing.

Public API:

    from core.engine.notify import send_notification

    send_notification("Task aos#3 completed", topic="work")
    send_notification("Disk 90% full", kind="alert")   # infers alerts topic

See router.py for the delivery tiers and fallback chain.
"""

from .router import send_notification, resolve_topic

__all__ = ["send_notification", "resolve_topic"]
