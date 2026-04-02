"""Simple token-bucket rate limiter for AOS service calls."""

import threading
import time


class RateLimiter:
    """Simple token-bucket rate limiter.

    Enforces a minimum interval between calls. Thread-safe.
    """

    def __init__(self, max_per_second: float = 1.0):
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        """Block until a call is allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()

    def try_acquire(self) -> bool:
        """Non-blocking: return True if allowed, False if rate limited."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_call >= self._interval:
                self._last_call = now
                return True
            return False
