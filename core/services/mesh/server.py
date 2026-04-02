"""meshd HTTP API.

Lightweight HTTP server (stdlib only) for mesh node communication.

Every node exposes:
    GET  /health              — local node health
    GET  /info                — node identity, version, uptime, capabilities
    POST /message             — receive a message from another node
    GET  /messages            — recent messages (last 50)
    POST /heartbeat           — receive heartbeat (admin only)

Admin node also exposes:
    GET  /fleet/health        — aggregated fleet health
    GET  /fleet/nodes         — full node roster
    POST /fleet/error         — receive error report from a node
    GET  /fleet/update/status — update rollout progress

Binds to the Tailscale/mesh interface only (not 0.0.0.0).
Default port: 4100.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PORT = 4100


class MeshHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the mesh service."""

    daemon = None  # set by MeshServer

    def do_GET(self):
        if self.path == "/health":
            self._respond_json(self._local_health())
        elif self.path == "/info":
            self._respond_json(self._node_info())
        elif self.path == "/messages":
            self._respond_json(self._recent_messages())
        elif self.path == "/fleet/health" and self._is_admin():
            self._respond_json(self._fleet_health())
        elif self.path == "/fleet/nodes" and self._is_admin():
            self._respond_json(self._fleet_nodes())
        elif self.path == "/fleet/update/status" and self._is_admin():
            self._respond_json(self._update_status())
        else:
            self._respond_json({"error": "Not found"}, 404)

    def do_POST(self):
        body = self._read_body()
        if body is None:
            return

        if self.path == "/message":
            self._handle_message(body)
        elif self.path == "/heartbeat" and self._is_admin():
            self._handle_heartbeat(body)
        elif self.path == "/fleet/error" and self._is_admin():
            self._handle_error_report(body)
        else:
            self._respond_json({"error": "Not found"}, 404)

    # --- Node endpoints ---

    def _local_health(self) -> dict:
        """Run local health check and return results."""
        # TODO: integrate with aos self-test
        return {
            "status": "healthy",
            "node": self.daemon.node_name,
            "uptime": int(time.time() - self.daemon._start_time),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _node_info(self) -> dict:
        """Return node identity and metadata."""
        version = "unknown"
        version_file = Path.home() / ".aos" / ".version"
        if version_file.exists():
            version = version_file.read_text().strip()

        return {
            "node": self.daemon.node_name,
            "role": self.daemon.role,
            "version": version,
            "uptime": int(time.time() - self.daemon._start_time),
            "port": self.daemon.port,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _recent_messages(self) -> dict:
        """Return last 50 messages."""
        return {
            "messages": list(self.daemon._messages),
            "count": len(self.daemon._messages),
        }

    def _handle_message(self, body: dict):
        """Receive and store a message from another node."""
        required = ["from", "type"]
        if not all(k in body for k in required):
            self._respond_json({"error": "Missing required fields: from, type"}, 400)
            return

        body["received_at"] = datetime.now(timezone.utc).isoformat()
        self.daemon._messages.append(body)

        log.info("Message from %s: type=%s", body.get("from"), body.get("type"))

        # Publish to local eventd if available
        self._forward_to_eventd(body)

        self._respond_json({"status": "received", "id": body.get("id", "")})

    # --- Admin endpoints ---

    def _handle_heartbeat(self, body: dict):
        """Receive heartbeat from a node (admin only)."""
        node = body.get("node")
        if not node:
            self._respond_json({"error": "Missing node name"}, 400)
            return

        body["last_seen"] = datetime.now(timezone.utc).isoformat()
        if hasattr(self.daemon, "_fleet"):
            self.daemon._fleet.update_node(node, body)

        self._respond_json({"status": "ok"})

    def _handle_error_report(self, body: dict):
        """Receive error report from a node (admin only)."""
        node = body.get("node", "unknown")
        error = body.get("error", "unknown error")
        log.warning("Error report from %s: %s", node, error)

        if hasattr(self.daemon, "_fleet"):
            self.daemon._fleet.record_error(node, body)

        # Forward to local eventd for alerting
        self._forward_to_eventd({
            "type": "mesh.node.error",
            "data": body,
            "source": f"mesh:{node}",
        })

        self._respond_json({"status": "recorded"})

    def _fleet_health(self) -> dict:
        """Return aggregated fleet health (admin only)."""
        if hasattr(self.daemon, "_fleet"):
            return self.daemon._fleet.get_health_summary()
        return {"error": "Fleet manager not available"}

    def _fleet_nodes(self) -> dict:
        """Return full node roster (admin only)."""
        if hasattr(self.daemon, "_fleet"):
            return self.daemon._fleet.get_roster()
        return {"error": "Fleet manager not available"}

    def _update_status(self) -> dict:
        """Return update rollout status (admin only)."""
        if hasattr(self.daemon, "_fleet"):
            return self.daemon._fleet.get_update_status()
        return {"error": "Fleet manager not available"}

    # --- Helpers ---

    def _is_admin(self) -> bool:
        """Check if this node is the admin."""
        return self.daemon.role == "admin"

    def _read_body(self) -> dict | None:
        """Read and parse JSON body."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError) as e:
            self._respond_json({"error": f"Invalid JSON: {e}"}, 400)
            return None

    def _respond_json(self, data: dict, status: int = 200):
        """Send a JSON response."""
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward_to_eventd(self, event: dict):
        """Forward an event to local eventd (best-effort)."""
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:4097/event",
                data=json.dumps(event).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # eventd may not be running

    def log_message(self, format, *args):
        """Suppress default HTTP access logs — we use our own logging."""
        pass


class MeshServer:
    """HTTP server wrapper for the mesh service."""

    def __init__(self, port: int = DEFAULT_PORT, daemon=None):
        self.port = port
        self.daemon = daemon
        daemon._start_time = time.time()
        daemon._messages = deque(maxlen=50)

    def start(self):
        """Start the HTTP server in a background thread."""
        MeshHandler.daemon = self.daemon

        # Bind to all interfaces on the mesh port
        # Security: Headscale/Tailscale ACLs control who can reach this port
        self._server = HTTPServer(("0.0.0.0", self.port), MeshHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="meshd-http",
            daemon=True,
        )
        self._thread.start()
        log.info("Mesh HTTP server listening on :%d", self.port)

    def stop(self):
        """Stop the HTTP server."""
        if hasattr(self, "_server"):
            self._server.shutdown()
            log.info("Mesh HTTP server stopped")
