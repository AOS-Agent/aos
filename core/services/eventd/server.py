"""eventd HTTP API.

Lightweight HTTP server (stdlib only, no dependencies) that allows
external services to publish events and check health.

Endpoints:
    POST /event     — Publish an event to the system bus
                      Body: {"type": "comms.message_received", "data": {...}, "source": "..."}
    GET  /health    — Bus health + active watchers + consumers
    GET  /events    — Recent events (last 50, for debugging)

Runs on localhost only. Default port: 4097.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

log = logging.getLogger(__name__)

DEFAULT_PORT = 4097


class EventdHandler(BaseHTTPRequestHandler):
    """HTTP request handler for eventd."""

    # Reference to the daemon (set by EventdServer)
    daemon = None

    def do_GET(self):
        if self.path == "/health":
            self._respond_json(self._health())
        elif self.path == "/events":
            self._respond_json(self._recent_events())
        else:
            self._respond_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/event":
            self._handle_publish()
        else:
            self._respond_json({"error": "Not found"}, 404)

    def _handle_publish(self):
        """Accept an event via POST and publish to system bus."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._respond_json({"error": f"Invalid JSON: {e}"}, 400)
            return

        event_type = data.get("type")
        if not event_type:
            self._respond_json({"error": "Missing 'type' field"}, 400)
            return

        from core.bus import Event, system_bus

        event = Event(
            type=event_type,
            data=data.get("data", {}),
            source=data.get("source", "http"),
            timestamp=datetime.now(),
        )

        delivered = system_bus.publish(event)
        self._respond_json({
            "ok": True,
            "event_id": event.id,
            "delivered_to": delivered,
        })

    def _health(self) -> dict:
        from core.bus import system_bus

        health = {
            "service": "eventd",
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "bus": system_bus.health(),
        }

        if self.daemon:
            health["watchers"] = {
                w.name: w.health() for w in self.daemon.watchers
            }

        return health

    def _recent_events(self) -> dict:
        from core.bus import system_bus

        events = system_bus.recent_events[-50:]
        return {
            "count": len(events),
            "events": [e.to_dict() for e in events],
        }

    def _respond_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default access log — use our own logger."""
        log.debug("HTTP %s", format % args)


class EventdServer:
    """Threaded HTTP server for eventd."""

    def __init__(self, port: int = DEFAULT_PORT, daemon=None):
        self.port = port
        self.daemon = daemon
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        EventdHandler.daemon = self.daemon
        self._server = HTTPServer(("127.0.0.1", self.port), EventdHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="eventd-http",
            daemon=True,
        )
        self._thread.start()
        log.info("eventd HTTP server started on localhost:%d", self.port)

    def stop(self):
        if self._server:
            self._server.shutdown()
            log.info("eventd HTTP server stopped")
