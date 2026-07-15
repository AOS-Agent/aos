"""Sentinel service entrypoint — runs the watcher and a fallback poller.

Primary driver: SentinelWatcher (kqueue on chat.db-wal) — instant detection.
Fallback driver: SentinelSpawner.run_forever at 60s — catches anything the
watcher misses (e.g., bug, restart gap).

Run with:
    python3 -m core.engine.comms.sentinel.service
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

LOG_DIR = Path.home() / ".aos" / "logs" / "sentinel"


def _setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "service.log")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fh.formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)


def main():
    for candidate in (Path.home() / "project" / "aos", Path.home() / "aos"):
        if (candidate / "core").is_dir():
            sys.path.insert(0, str(candidate))
            break

    _setup_logging()
    log = logging.getLogger("sentinel.service")
    log.info("Sentinel service starting (watcher + fallback)")

    from core.engine.comms.sentinel.spawner import SentinelSpawner
    from core.engine.comms.sentinel.watcher import SentinelWatcher

    watcher = SentinelWatcher()
    spawner = SentinelSpawner()

    def _shutdown(signum, frame):
        log.info("Shutdown signal received")
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Spawner fallback in background
    t_fallback = threading.Thread(
        target=spawner.run_forever, kwargs={"interval_sec": 60},
        daemon=True, name="sentinel-fallback",
    )
    t_fallback.start()
    log.info("Fallback spawner thread started")

    # Watcher in foreground (blocks)
    watcher.run()


if __name__ == "__main__":
    main()
