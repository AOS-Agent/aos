"""n8n execution watcher — monitors automation workflow executions.

Polls the n8n API for new execution results and publishes events:
- automation.execution.completed — workflow ran successfully
- automation.execution.failed — workflow execution errored

Events are consumed by Qareen to update automation status cards
and by the notification system to alert the operator on failures.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime

from ..watcher import BaseWatcher

log = logging.getLogger(__name__)

N8N_API_URL = "http://127.0.0.1:5678/api/v1"
POLL_INTERVAL = 15  # seconds
HEALTH_URL = "http://127.0.0.1:5678/healthz"


def _get_api_key() -> str | None:
    """Read the n8n API key from Keychain."""
    import subprocess
    from pathlib import Path

    agent_secret = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"
    try:
        result = subprocess.run(
            [str(agent_secret), "get", "N8N_API_KEY"],
            capture_output=True, text=True, timeout=5,
        )
        key = result.stdout.strip()
        return key if key and result.returncode == 0 else None
    except Exception:
        return None


class N8nWatcher(BaseWatcher):
    """Watches n8n for new workflow execution results."""

    name = "n8n"
    domain = "automations"

    def __init__(self):
        super().__init__()
        self._api_key: str | None = None
        self._last_seen_id: str | None = None
        self._last_poll: datetime | None = None
        self._n8n_available = False
        self._consecutive_failures = 0

    def _n8n_healthy(self) -> bool:
        """Check if n8n is responding."""
        try:
            resp = urllib.request.urlopen(HEALTH_URL, timeout=3)
            return resp.status == 200
        except Exception:
            return False

    def _fetch_executions(self, limit: int = 10) -> list[dict]:
        """Fetch recent executions from n8n API."""
        if not self._api_key:
            return []

        url = f"{N8N_API_URL}/executions?limit={limit}"
        req = urllib.request.Request(url, headers={
            "X-N8N-API-KEY": self._api_key,
            "Accept": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("data", [])
        except Exception as e:
            log.debug("Failed to fetch n8n executions: %s", e)
            return []

    def start(self) -> None:
        """Poll n8n for execution results."""
        self._api_key = _get_api_key()
        if not self._api_key:
            log.warning("n8n watcher: no API key found, will retry periodically")

        log.info("n8n watcher started — polling every %ds", POLL_INTERVAL)

        while self._running:
            try:
                # Check n8n availability
                if not self._n8n_healthy():
                    if self._n8n_available:
                        log.warning("n8n became unavailable")
                        self._n8n_available = False
                    time.sleep(POLL_INTERVAL)
                    continue

                if not self._n8n_available:
                    log.info("n8n is available")
                    self._n8n_available = True

                # Retry API key if we don't have one
                if not self._api_key:
                    self._api_key = _get_api_key()
                    if not self._api_key:
                        time.sleep(POLL_INTERVAL)
                        continue

                # Fetch recent executions
                executions = self._fetch_executions(limit=10)
                self._last_poll = datetime.now()

                for execution in executions:
                    exec_id = execution.get("id")
                    if not exec_id:
                        continue

                    # Skip already-seen executions
                    if self._last_seen_id and exec_id <= self._last_seen_id:
                        continue

                    status = execution.get("status", "")
                    if status in ("running", "waiting", "new"):
                        continue  # Not finished yet

                    # Publish event
                    workflow = execution.get("workflowData", {})
                    wf_name = workflow.get("name", "Unknown")
                    wf_id = execution.get("workflowId", "")

                    if status == "success":
                        self._publish_event("automation.execution.completed", {
                            "execution_id": exec_id,
                            "workflow_id": wf_id,
                            "workflow_name": wf_name,
                            "status": "success",
                            "finished_at": execution.get("stoppedAt"),
                        })
                    elif status in ("error", "crashed"):
                        error_msg = ""
                        if execution.get("data", {}).get("resultData", {}).get("error"):
                            error_msg = execution["data"]["resultData"]["error"].get("message", "")
                        self._publish_event("automation.execution.failed", {
                            "execution_id": exec_id,
                            "workflow_id": wf_id,
                            "workflow_name": wf_name,
                            "status": "error",
                            "error": error_msg,
                            "finished_at": execution.get("stoppedAt"),
                        })

                # Update high water mark
                if executions:
                    self._last_seen_id = executions[0].get("id")

                self._consecutive_failures = 0

            except Exception as e:
                self._consecutive_failures += 1
                log.error(
                    "n8n watcher error (attempt %d): %s",
                    self._consecutive_failures, e,
                )

            time.sleep(POLL_INTERVAL)

    def _publish_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the system bus."""
        try:
            from core.bus import Event, system_bus
            system_bus.publish(Event(
                type=event_type,
                data=data,
                source="n8n_watcher",
            ))
            log.info("Published %s: %s (%s)", event_type, data.get("workflow_name"), data.get("status"))
        except ImportError:
            log.debug("System bus not available — event not published: %s", event_type)
        except Exception as e:
            log.error("Failed to publish event: %s", e)

    def stop(self) -> None:
        """Clean up."""
        log.info("n8n watcher stopping")

    def health(self) -> dict:
        """Return watcher health status."""
        return {
            "name": self.name,
            "domain": self.domain,
            "running": self._running,
            "n8n_available": self._n8n_available,
            "api_key_configured": self._api_key is not None,
            "last_poll": self._last_poll.isoformat() if self._last_poll else None,
            "last_seen_execution": self._last_seen_id,
            "consecutive_failures": self._consecutive_failures,
        }
