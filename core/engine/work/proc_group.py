"""Process-group discipline — a domain-agnostic worker registry with kill-safe
teardown. Generalised from the comms enrich engine's ``LiveGroups`` (which the
islah dossier and Kanban §3.7 both name as the proven local pattern).

WHY PROCESS GROUPS. When a driver is killed mid-run, its in-flight child
processes are reparented to launchd/init and KEEP RUNNING — for a spawned
``claude`` worker that means quota kept burning with nothing watching it.
``claude --print`` also spawns its own node subprocess, so killing just the
``claude`` pid can leave that grandchild alive. Every worker is therefore
spawned in its OWN session/process group (``start_new_session=True`` → the child
is a group leader, ``pgid == pid``), tracked here, and torn down with ``killpg``
on the whole group. A SIGTERM/SIGINT to the runner propagates to every live
group before the runner exits, so a killed or redeployed runner never orphans a
worker.

This module knows nothing about work, comms, or claude — it is a reusable
substrate. A caller registers a group leader's pid when it spawns, unregisters
it when the worker is reaped normally, and installs signal handlers so an abrupt
exit tears every remaining group down.
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Any, Callable


def terminate_group(pid: int, sig: int = signal.SIGTERM) -> bool:
    """Signal the whole process group led by ``pid``. Returns True if the signal
    was delivered, False if the group was already gone. Swallows races and
    permission errors — killing an already-dead group is a no-op, not an error.
    Kills a ``claude`` worker AND its node grandchildren in one call."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class ProcessGroupRegistry:
    """Thread-safe registry of live worker process groups + signal teardown.

    A killed/redeployed run must not leave worker children behind, so on
    SIGTERM/SIGINT every registered group is killed before the process exits.
    """

    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._lock = threading.Lock()
        self._shutting_down = False

    def register(self, pid: int) -> None:
        with self._lock:
            self._pids.add(pid)

    def unregister(self, pid: int) -> None:
        with self._lock:
            self._pids.discard(pid)

    @property
    def shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    @property
    def live_pids(self) -> list[int]:
        with self._lock:
            return sorted(self._pids)

    def terminate_all(self, sig: int = signal.SIGTERM) -> list[int]:
        """Kill every live group. Returns the pids that were signalled. Marks the
        registry as shutting down so no further workers are started."""
        with self._lock:
            self._shutting_down = True
            pids = list(self._pids)
        for pid in pids:
            terminate_group(pid, sig)
        return pids

    def install_signal_handlers(self) -> Callable[[], None]:
        """Install SIGTERM/SIGINT handlers that tear down all groups, then
        re-raise the default disposition so the process actually exits. Returns a
        ``restore()`` to undo them. No-ops off the main thread (e.g. a test
        runner), where signal handlers cannot be installed."""
        prev: dict[int, Any] = {}

        def handler(signum, _frame):
            self.terminate_all()
            signal.signal(signum, prev.get(signum, signal.SIG_DFL))
            os.kill(os.getpid(), signum)

        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                prev[s] = signal.getsignal(s)
                signal.signal(s, handler)
            except (ValueError, OSError):
                pass

        def restore() -> None:
            for s, h in prev.items():
                try:
                    signal.signal(s, h)
                except (ValueError, OSError):
                    pass

        return restore
