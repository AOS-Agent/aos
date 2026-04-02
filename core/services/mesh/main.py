#!/usr/bin/env python3
"""meshd — The AOS Mesh Service.

Connects this AOS node to the mesh network. Handles heartbeat,
health reporting, node-to-node messaging, and fleet management.

Runs on every node as a LaunchAgent on port 4100.

Usage:
    python3 -m core.services.mesh.main          # foreground
    core/bin/internal/meshd                       # via wrapper
"""

import logging
import logging.handlers
import os
import signal
import sys
import time
from pathlib import Path

# Ensure AOS root is importable
AOS_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(AOS_ROOT))

# Set process title
try:
    import setproctitle
    setproctitle.setproctitle("meshd")
except ImportError:
    pass

# Configure logging
LOG_DIR = Path.home() / ".aos" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "meshd.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)

log = logging.getLogger("meshd")

# Instance config paths
MESH_CONFIG = Path.home() / ".aos" / "config" / "mesh.yaml"
MESH_DATA = Path.home() / ".aos" / "mesh"

DEFAULT_PORT = 4100


class MeshDaemon:
    """The AOS Mesh Service."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._running = False
        self.config = self._load_config()
        self.role = self.config.get("role", "node")  # "admin" or "node"
        self.node_name = self.config.get("node_name", "unknown")
        self.admin_node = self.config.get("admin_node", None)

    def _load_config(self) -> dict:
        """Load mesh config from ~/.aos/config/mesh.yaml."""
        if not MESH_CONFIG.exists():
            log.warning("No mesh config at %s — running in standalone mode", MESH_CONFIG)
            return {}
        try:
            import yaml
            return yaml.safe_load(MESH_CONFIG.read_text()) or {}
        except Exception as e:
            log.error("Failed to load mesh config: %s", e)
            return {}

    def start(self):
        """Start the mesh service."""
        log.info("meshd starting (pid=%d, node=%s, role=%s)",
                 os.getpid(), self.node_name, self.role)

        # Ensure data directories exist
        MESH_DATA.mkdir(parents=True, exist_ok=True)
        (MESH_DATA / "messages").mkdir(exist_ok=True)
        (MESH_DATA / "feed").mkdir(exist_ok=True)

        # Start HTTP server
        from core.services.mesh.server import MeshServer
        self._http = MeshServer(port=self.port, daemon=self)
        self._http.start()

        # Start heartbeat (if connected to a mesh)
        if self.admin_node:
            from core.services.mesh.heartbeat import HeartbeatSender
            self._heartbeat = HeartbeatSender(daemon=self)
            self._heartbeat.start()

        # If admin, start fleet health collector
        if self.role == "admin":
            from core.services.mesh.fleet import FleetManager
            self._fleet = FleetManager(daemon=self)
            self._fleet.start()

        self._running = True
        log.info("meshd ready — role=%s, HTTP on :%d", self.role, self.port)

    def stop(self):
        """Graceful shutdown."""
        log.info("meshd shutting down...")
        self._running = False

        if hasattr(self, "_heartbeat"):
            self._heartbeat.stop()
        if hasattr(self, "_fleet"):
            self._fleet.stop()
        if hasattr(self, "_http"):
            self._http.stop()

        log.info("meshd stopped")

    def run_forever(self):
        """Block until signal received."""
        self.start()

        def _shutdown(signum, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


def main():
    port = int(os.environ.get("MESHD_PORT", str(DEFAULT_PORT)))
    daemon = MeshDaemon(port=port)
    daemon.run_forever()


if __name__ == "__main__":
    main()
