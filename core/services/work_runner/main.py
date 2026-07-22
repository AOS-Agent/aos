"""work-runner service entrypoint — the generic runner's daemon wrapper.

Mirrors the Sentinel service shape: set up logging + signal handlers, build the
runner, and run its poll loop. The loop itself owns the poll interval, the
process-group teardown, and the kill-switch re-read (so a live ``enabled: false``
edit pauses spawning without a restart).

Run with:
    python3 -m core.services.work_runner.main

Ships OFF. The runner will idle (poll, spawn nothing) until
~/.aos/config/work-runner.yaml sets ``enabled: true`` — autonomous agent
spawning is opt-in per the trust philosophy.
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

LOG_DIR = Path.home() / ".aos" / "logs" / "work-runner"


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
    # Repo-root bootstrap so core.* imports resolve whether run from ~/aos
    # (runtime) or the dev worktree.
    for candidate in (Path.home() / "aos", Path.home() / "project" / "aos"):
        if (candidate / "core").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            break

    _setup_logging()
    log = logging.getLogger("work-runner.service")

    from core.engine.work.runner import RunnerConfig, WorkRunner

    cfg = RunnerConfig.load()
    runner = WorkRunner(cfg)
    log.info("work-runner service starting (enabled=%s)", cfg.enabled)

    def _shutdown(signum, _frame):
        log.info("shutdown signal received")
        runner.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    runner.run_forever()


if __name__ == "__main__":
    main()
