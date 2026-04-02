"""Fleet Manager — admin-only fleet health tracking.

Maintains a roster of all mesh nodes, their health status, and error history.
Only runs on the admin node.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROSTER_FILE = Path.home() / ".aos" / "mesh" / "roster.json"
OFFLINE_THRESHOLD = 180  # 3 minutes without heartbeat = offline


class FleetManager:
    """Tracks fleet health and manages node roster."""

    def __init__(self, daemon):
        self.daemon = daemon
        self._roster: dict[str, dict] = {}
        self._errors: list[dict] = []
        self._update_status: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._load_roster()

    def start(self):
        self._running = True
        # Background thread to mark nodes offline
        self._checker = threading.Thread(
            target=self._check_loop,
            name="meshd-fleet-checker",
            daemon=True,
        )
        self._checker.start()
        log.info("Fleet manager started (%d known nodes)", len(self._roster))

    def stop(self):
        self._running = False
        self._save_roster()

    def update_node(self, node: str, data: dict):
        """Update node status from heartbeat."""
        with self._lock:
            self._roster[node] = {
                "node": node,
                "ip": data.get("ip", ""),
                "version": data.get("version", "unknown"),
                "health": data.get("health", "unknown"),
                "errors": data.get("errors", []),
                "uptime": data.get("uptime", 0),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "status": "online",
            }
            self._save_roster()

    def record_error(self, node: str, data: dict):
        """Record an error report from a node."""
        with self._lock:
            entry = {
                "node": node,
                "error": data.get("error", "unknown"),
                "details": data.get("details", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._errors.append(entry)
            # Keep last 100 errors
            if len(self._errors) > 100:
                self._errors = self._errors[-100:]

            # Update roster health
            if node in self._roster:
                self._roster[node]["health"] = "error"
                self._roster[node]["errors"] = data.get("errors", [data.get("error", "")])

            log.warning("Fleet error from %s: %s", node, data.get("error"))

    def get_health_summary(self) -> dict:
        """Return aggregated fleet health."""
        with self._lock:
            nodes = list(self._roster.values())
            online = [n for n in nodes if n.get("status") == "online"]
            warnings = [n for n in nodes if n.get("health") == "warning"]
            errors = [n for n in nodes if n.get("health") == "error"]

            return {
                "total": len(nodes),
                "online": len(online),
                "offline": len(nodes) - len(online),
                "healthy": len([n for n in online if n.get("health") == "healthy"]),
                "warnings": len(warnings),
                "errors": len(errors),
                "nodes": nodes,
                "recent_errors": self._errors[-10:],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_roster(self) -> dict:
        """Return full node roster."""
        with self._lock:
            return {
                "nodes": list(self._roster.values()),
                "count": len(self._roster),
            }

    def get_update_status(self) -> dict:
        """Return update rollout status."""
        with self._lock:
            return self._update_status or {"status": "no active rollout"}

    def _check_loop(self):
        """Periodically check for offline nodes."""
        while self._running:
            self._mark_offline_nodes()
            time.sleep(30)

    def _mark_offline_nodes(self):
        """Mark nodes as offline if no heartbeat received recently."""
        now = datetime.now(timezone.utc)
        with self._lock:
            for node, data in self._roster.items():
                last_seen = data.get("last_seen")
                if not last_seen:
                    continue
                try:
                    seen_time = datetime.fromisoformat(last_seen)
                    delta = (now - seen_time).total_seconds()
                    if delta > OFFLINE_THRESHOLD and data.get("status") != "offline":
                        data["status"] = "offline"
                        log.info("Node %s marked offline (last seen %ds ago)", node, int(delta))
                except (ValueError, TypeError):
                    pass

    def _load_roster(self):
        """Load persisted roster from disk."""
        if ROSTER_FILE.exists():
            try:
                data = json.loads(ROSTER_FILE.read_text())
                self._roster = {n["node"]: n for n in data.get("nodes", [])}
                # Mark all as offline on startup (they'll heartbeat back)
                for node in self._roster.values():
                    node["status"] = "offline"
            except Exception as e:
                log.warning("Failed to load roster: %s", e)

    def _save_roster(self):
        """Persist roster to disk."""
        try:
            ROSTER_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {"nodes": list(self._roster.values())}
            ROSTER_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            log.warning("Failed to save roster: %s", e)
