#!/usr/bin/env python3
"""comms-bus — The AOS Communications Bus.

Always-on service that polls all communication channels, merges messages
into a unified stream, and delivers them through the trust cascade.

Consumers (auto-registered by MessageBus):
  - PeopleIntelConsumer: logs interactions to people DB
  - PatternUpdateConsumer: updates communication patterns
  - CommsOrchestrator: trust cascade (L0 observe → L3 autonomous)

Usage:
    python3 -m core.services.comms_bus.main          # foreground
    core/bin/internal/comms-bus                        # via wrapper

Process name: Shows as 'comms-bus' in Activity Monitor.
"""

import json
import logging
import logging.handlers
import os
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure AOS root is importable
AOS_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(AOS_ROOT))

# Set process title
try:
    import setproctitle
    setproctitle.setproctitle("comms-bus")
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
            LOG_DIR / "comms_bus.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)

log = logging.getLogger("comms-bus")

DEFAULT_POLL_INTERVAL = 300  # 5 minutes
DEFAULT_PORT = 4099


class CommsBusDaemon:
    """Always-on communications bus daemon."""

    def __init__(self, poll_interval: int = DEFAULT_POLL_INTERVAL, port: int = DEFAULT_PORT):
        self.poll_interval = poll_interval
        self.port = port
        self.bus = None
        self._running = False
        self._poll_count = 0
        self._last_message_count = 0
        self._started_at: str | None = None

        # --- Live stats ---
        self._stats_per_channel: dict[str, dict] = {}  # channel -> {total, last_ts, last_count}
        self._stats_history: list[dict] = []  # last N poll results

    def start(self):
        """Initialize the bus, start HTTP server, begin polling."""
        log.info("comms-bus starting (pid=%d, poll_interval=%ds)",
                 os.getpid(), self.poll_interval)

        from core.engine.comms.bus import MessageBus
        self.bus = MessageBus(auto_register=True)

        log.info("Loaded %d adapter(s), %d consumer(s)",
                 len(self.bus.adapters),
                 len(self.bus.consumers))

        # Start HTTP health server
        from core.services.comms_bus.server import CommsBusServer
        self._http = CommsBusServer(port=self.port, daemon=self)
        self._http.start()

        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()

        # Seed per-channel stats from comms.db
        self._seed_stats_from_db()

        log.info("comms-bus ready — %d adapters, %d consumers, HTTP on :%d",
                 len(self.bus.adapters), len(self.bus.consumers), self.port)

    def poll_once(self) -> int:
        """Run a single poll cycle. Returns message count."""
        try:
            messages = self.bus.poll()
            self._poll_count += 1
            self._last_message_count = len(messages)

            # Track per-channel stats
            poll_breakdown: dict[str, int] = defaultdict(int)
            for msg in messages:
                ch = msg.channel
                poll_breakdown[ch] += 1
                if ch not in self._stats_per_channel:
                    self._stats_per_channel[ch] = {"total": 0, "last_ts": None, "last_count": 0, "session_total": 0}
                self._stats_per_channel[ch]["session_total"] += 1
                self._stats_per_channel[ch]["last_count"] = poll_breakdown[ch]
                if msg.timestamp:
                    ts = msg.timestamp.isoformat() if hasattr(msg.timestamp, 'isoformat') else str(msg.timestamp)
                    cur = self._stats_per_channel[ch].get("last_ts")
                    if cur is None or ts > cur:
                        self._stats_per_channel[ch]["last_ts"] = ts

            # Store poll in history (keep last 50)
            self._stats_history.append({
                "poll": self._poll_count,
                "at": datetime.now(timezone.utc).isoformat(),
                "total": len(messages),
                "channels": dict(poll_breakdown),
            })
            if len(self._stats_history) > 50:
                self._stats_history = self._stats_history[-50:]

            if messages:
                log.info("Poll #%d: %d messages processed", self._poll_count, len(messages))
            else:
                log.debug("Poll #%d: no new messages", self._poll_count)

            return len(messages)
        except Exception as e:
            log.error("Poll error: %s", e, exc_info=True)
            return 0

    def _seed_stats_from_db(self):
        """Load initial channel stats from comms.db on startup."""
        db_path = Path.home() / ".aos" / "data" / "comms.db"
        if not db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT channel,
                       COUNT(*) as total,
                       MAX(timestamp) as last_ts,
                       SUM(CASE WHEN timestamp > datetime('now', '-1 day') THEN 1 ELSE 0 END) as last_24h,
                       SUM(CASE WHEN timestamp > datetime('now', '-7 days') THEN 1 ELSE 0 END) as last_7d
                FROM messages
                GROUP BY channel
            """).fetchall()
            for row in rows:
                self._stats_per_channel[row["channel"]] = {
                    "total": row["total"],
                    "last_ts": row["last_ts"],
                    "last_count": 0,
                    "session_total": 0,
                    "last_24h": row["last_24h"],
                    "last_7d": row["last_7d"],
                }
            conn.close()
            log.info("Seeded stats from comms.db: %d channels", len(self._stats_per_channel))
        except Exception as e:
            log.warning("Could not seed stats from comms.db: %s", e)

    def stats(self) -> dict:
        """Return live ingestion stats for all channels."""
        return {
            "started_at": self._started_at,
            "poll_count": self._poll_count,
            "poll_interval_seconds": self.poll_interval,
            "channels": self._stats_per_channel,
            "recent_polls": self._stats_history[-10:],
        }

    def stop(self):
        """Graceful shutdown."""
        log.info("comms-bus shutting down...")
        self._running = False

        if hasattr(self, '_http'):
            self._http.stop()

        log.info("comms-bus stopped (total polls: %d)", self._poll_count)

    def run_forever(self):
        """Start and poll in a loop until signal."""
        self.start()

        def _shutdown(signum, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        # Initial poll immediately
        self.poll_once()

        try:
            while self._running:
                time.sleep(self.poll_interval)
                if self._running:
                    self.poll_once()
        except KeyboardInterrupt:
            self.stop()

    def health(self) -> dict:
        """Return daemon health info."""
        h = {
            "service": "comms-bus",
            "status": "running" if self._running else "stopped",
            "poll_count": self._poll_count,
            "poll_interval_seconds": self.poll_interval,
            "last_message_count": self._last_message_count,
        }
        if self.bus:
            h["bus"] = self.bus.health()
        return h


def main():
    poll_interval = int(os.environ.get("COMMS_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))
    port = int(os.environ.get("COMMS_BUS_PORT", str(DEFAULT_PORT)))
    daemon = CommsBusDaemon(poll_interval=poll_interval, port=port)
    daemon.run_forever()


if __name__ == "__main__":
    main()
