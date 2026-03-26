#!/usr/bin/env python3
"""eventd — The AOS Event Daemon.

Central nervous system of AOS. Runs 24/7 as a LaunchAgent.
Auto-discovers watchers (event sources) and consumers (event handlers),
runs the system bus in-process, exposes an HTTP API for external services.

Usage:
    python3 -m core.services.eventd.main          # foreground
    core/bin/eventd                                 # via wrapper (sets process name)

Process name: Shows as 'eventd' in Activity Monitor (via setproctitle or exec -a).
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

# Set process title before anything else
try:
    import setproctitle
    setproctitle.setproctitle("eventd")
except ImportError:
    pass  # Shell wrapper handles it as fallback

# Configure logging
LOG_DIR = Path.home() / ".aos" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "eventd.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)

log = logging.getLogger("eventd")


class EventDaemon:
    """The AOS event daemon."""

    def __init__(self, port: int = 4097):
        self.port = port
        self.watchers = []
        self.consumers = []
        self._running = False

    def start(self):
        """Start the daemon: discover, subscribe, serve."""
        log.info("eventd starting (pid=%d)", os.getpid())

        # Import bus
        from core.bus import system_bus

        # Auto-discover consumers
        from core.services.eventd.discovery import discover_consumers
        self.consumers = discover_consumers()
        for consumer in self.consumers:
            system_bus.subscribe(consumer)
        log.info("Loaded %d consumer(s): %s",
                 len(self.consumers),
                 [c.name for c in self.consumers])

        # Auto-discover watchers
        from core.services.eventd.discovery import discover_watchers
        self.watchers = discover_watchers()
        log.info("Loaded %d watcher(s): %s",
                 len(self.watchers),
                 [w.name for w in self.watchers])

        # Start HTTP server
        from core.services.eventd.server import EventdServer
        self._http = EventdServer(port=self.port, daemon=self)
        self._http.start()

        # Start all watchers
        for watcher in self.watchers:
            watcher.run()

        self._running = True
        log.info("eventd ready — %d watchers, %d consumers, HTTP on :%d",
                 len(self.watchers), len(self.consumers), self.port)

    def stop(self):
        """Graceful shutdown."""
        log.info("eventd shutting down...")
        self._running = False

        # Stop watchers
        for watcher in self.watchers:
            watcher.shutdown()

        # Stop HTTP
        if hasattr(self, '_http'):
            self._http.stop()

        log.info("eventd stopped")

    def run_forever(self):
        """Block until signal received."""
        self.start()

        # Handle signals
        def _shutdown(signum, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        # Keep alive
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


def main():
    port = int(os.environ.get("EVENTD_PORT", "4097"))
    daemon = EventDaemon(port=port)
    daemon.run_forever()


if __name__ == "__main__":
    main()
