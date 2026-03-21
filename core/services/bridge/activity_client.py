"""Activity logging client — logs agent actions to the dashboard via HTTP."""

import logging

import httpx

logger = logging.getLogger(__name__)

DASHBOARD_URL = "http://localhost:4096"


def log_activity(agent: str, action: str, parent_agent: str = None,
                 status: str = "completed", summary: str = None) -> int | None:
    """Log an activity to the dashboard. Returns activity ID or None if dashboard is down."""
    try:
        params = {"agent": agent, "action": action, "status": status}
        if parent_agent:
            params["parent_agent"] = parent_agent
        if summary:
            params["summary"] = summary
        r = httpx.post(f"{DASHBOARD_URL}/api/activity", params=params, timeout=3)
        return r.json().get("id")
    except Exception as e:
        logger.debug(f"Activity log failed (dashboard down?): {e}")
        return None


def update_activity(activity_id: int, status: str, summary: str = None,
                    duration_ms: int = None):
    """Update an activity's status. Silently fails if dashboard is down."""
    if activity_id is None:
        return
    try:
        params = {"status": status}
        if summary:
            params["summary"] = summary
        if duration_ms is not None:
            params["duration_ms"] = str(duration_ms)
        httpx.patch(f"{DASHBOARD_URL}/api/activity/{activity_id}",
                    params=params, timeout=3)
    except Exception as e:
        logger.debug(f"Activity update failed: {e}")


def log_conversation(user_key: str, agent: str = None, topic_name: str = None,
                     message: str = "", response: str = None,
                     duration_ms: int = None, message_type: str = "text") -> int | None:
    """Log a conversation exchange. Returns conversation ID or None."""
    try:
        r = httpx.post(f"{DASHBOARD_URL}/api/conversations", json={
            "channel": "telegram",
            "user_key": user_key,
            "agent": agent,
            "topic_name": topic_name,
            "message": message,
            "response": response,
            "duration_ms": duration_ms,
            "message_type": message_type,
        }, timeout=5)
        return r.json().get("id")
    except Exception as e:
        logger.debug(f"Conversation log failed: {e}")
        return None


def update_conversation(conv_id: int, response: str, duration_ms: int = None):
    """Update a conversation with the response."""
    if conv_id is None:
        return
    try:
        httpx.patch(f"{DASHBOARD_URL}/api/conversations/{conv_id}", json={
            "response": response,
            "duration_ms": duration_ms,
        }, timeout=5)
    except Exception as e:
        logger.debug(f"Conversation update failed: {e}")
