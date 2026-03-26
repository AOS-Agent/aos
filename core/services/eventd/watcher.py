"""Base watcher interface for eventd.

Watchers monitor external sources (file changes, HTTP endpoints, etc.)
and publish events to the system bus when something happens.

To add a new watcher:
1. Create a file in core/services/eventd/watchers/
2. Subclass BaseWatcher
3. Implement start(), stop(), health()
4. Restart eventd — auto-discovery picks it up
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class BaseWatcher(ABC):
    """Base class for event source watchers."""

    name: str = ""
    domain: str = ""  # Which domain this watcher feeds (comms, schedule, etc.)

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def run(self) -> None:
        """Start the watcher in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"watcher-{self.name}",
            daemon=True,
        )
        self._thread.start()
        log.info("Watcher started: %s", self.name)

    def _run_loop(self):
        """Internal loop wrapper with error handling."""
        try:
            self.start()
        except Exception as e:
            log.error("Watcher %s crashed: %s", self.name, e, exc_info=True)
            self._running = False

    def shutdown(self) -> None:
        """Stop the watcher gracefully."""
        self._running = False
        try:
            self.stop()
        except Exception as e:
            log.error("Watcher %s stop error: %s", self.name, e)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("Watcher stopped: %s", self.name)

    @abstractmethod
    def start(self) -> None:
        """Start watching. Called in a background thread.
        Should loop while self._running is True."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Clean up resources when shutting down."""
        ...

    @abstractmethod
    def health(self) -> dict:
        """Return health status."""
        ...

    def __repr__(self) -> str:
        status = "running" if self._running else "stopped"
        return f"<{self.__class__.__name__} [{self.name}] {status}>"
