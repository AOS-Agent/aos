"""comms-bus HTTP API.

Lightweight health and control server (stdlib only).

Endpoints:
    GET  /health     — Bus health: adapters, consumers, poll stats
    GET  /api/stats  — Live ingestion stats per channel (for dashboards)
    POST /poll       — Trigger an immediate poll cycle
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

DEFAULT_PORT = 4099


class CommsBusHandler(BaseHTTPRequestHandler):
    """HTTP request handler for comms-bus."""

    daemon = None

    def do_GET(self):
        if self.path == "/health":
            self._respond_json(self._health())
        elif self.path == "/api/stats":
            self._respond_json(self._stats())
        else:
            self._respond_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/poll":
            self._handle_poll()
        else:
            self._respond_json({"error": "Not found"}, 404)

    def _handle_poll(self):
        """Trigger an immediate poll cycle."""
        if not self.daemon or not self.daemon.bus:
            self._respond_json({"error": "Bus not initialized"}, 503)
            return

        count = self.daemon.poll_once()
        self._respond_json({
            "ok": True,
            "messages_processed": count,
            "timestamp": datetime.now().isoformat(),
        })

    def _health(self) -> dict:
        health = {
            "service": "comms-bus",
            "timestamp": datetime.now().isoformat(),
        }
        if self.daemon:
            health.update(self.daemon.health())
        return health

    def _stats(self) -> dict:
        if not self.daemon:
            return {"error": "Bus not initialized"}
        return {
            "timestamp": datetime.now().isoformat(),
            **self.daemon.stats(),
        }

    def _respond_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default access log."""
        log.debug("HTTP %s", format % args)


class CommsBusServer:
    """Threaded HTTP server for comms-bus."""

    def __init__(self, port: int = DEFAULT_PORT, daemon=None):
        self.port = port
        self.daemon = daemon
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        CommsBusHandler.daemon = self.daemon
        self._server = HTTPServer(("127.0.0.1", self.port), CommsBusHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="comms-bus-http",
            daemon=True,
        )
        self._thread.start()
        log.info("comms-bus HTTP server started on localhost:%d", self.port)

    def stop(self):
        if self._server:
            self._server.shutdown()
            log.info("comms-bus HTTP server stopped")
