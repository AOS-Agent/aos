"""Heartbeat — periodic "I'm alive" signal to admin node.

Every 60 seconds, sends node health to the admin node's /heartbeat endpoint.
Includes: node name, AOS version, health status, current errors, uptime.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60  # seconds
OFFLINE_THRESHOLD = 180  # 3 minutes = offline


class HeartbeatSender:
    """Sends periodic heartbeat to admin node."""

    def __init__(self, daemon):
        self.daemon = daemon
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="meshd-heartbeat",
            daemon=True,
        )
        self._thread.start()
        log.info("Heartbeat sender started (interval=%ds, admin=%s)",
                 HEARTBEAT_INTERVAL, self.daemon.admin_node)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            try:
                self._send()
            except Exception as e:
                log.warning("Heartbeat failed: %s", e)
            time.sleep(HEARTBEAT_INTERVAL)

    def _send(self):
        """Send heartbeat to admin node."""
        version = "unknown"
        version_file = Path.home() / ".aos" / ".version"
        if version_file.exists():
            version = version_file.read_text().strip()

        health, errors = self._check_health()

        payload = {
            "node": self.daemon.node_name,
            "version": version,
            "health": health,
            "errors": errors,
            "uptime": int(time.time() - self.daemon._start_time),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        admin_url = f"http://{self.daemon.admin_node}:4100/heartbeat"
        req = urllib.request.Request(
            admin_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        log.debug("Heartbeat sent to %s", admin_url)

    def _check_health(self) -> tuple[str, list[str]]:
        """Run basic health checks, return (status, errors)."""
        errors = []

        # Check critical AOS services
        services = [
            ("eventd", 4097),
            ("dashboard", 4096),
        ]

        for name, port in services:
            try:
                url = f"http://127.0.0.1:{port}/health"
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                errors.append(f"{name} not responding on :{port}")

        if errors:
            return "error" if len(errors) > 1 else "warning", errors
        return "healthy", []
