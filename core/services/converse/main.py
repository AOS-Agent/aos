"""Converse supervisor — LaunchAgent entrypoint (com.aos.converse).

Mirrors the Sentinel/work-runner service shape: bootstrap sys.path, set up
file logging, build a Supervisor against the REAL channels (iMessage +
Slack), install signal handlers, run forever.

Ships OFF: the LaunchAgent (config/launchagents/com.aos.converse.plist.template
under core/services/converse/) is installed by migration 101 with
RunAtLoad=false and Disabled=true — feature-complete but inert until the
operator runs the Sana cutover (PLAN.md §8 Phase B / Wave 3 T5). Even once
loaded, the daemon itself idles (crash-sweep only, no channel watchers) if
~/.aos/config/converse.yaml has `enabled: false` — the same belt-and-
suspenders kill-switch pattern as work-runner.

Run with:
    python3 -m core.services.converse.main
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

LOG_DIR = Path.home() / ".aos" / "logs" / "converse"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "service.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fh.formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)


def main() -> None:
    for candidate in (Path.home() / "project" / "aos", Path.home() / "aos"):
        if (candidate / "core").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            break

    _setup_logging()
    log = logging.getLogger("converse.service")
    log.info("Converse service starting")

    from core.services.converse.supervisor import Supervisor

    supervisor = Supervisor()

    def _shutdown(signum, frame):
        log.info("Shutdown signal received")
        supervisor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    supervisor.run_forever()


if __name__ == "__main__":
    main()
