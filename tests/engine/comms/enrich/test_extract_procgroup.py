"""Process-group teardown + parsing.

The kill test is the load-bearing one: sample §8 showed a killed driver orphans
in-flight `claude` (and its node) children, which keep burning subscription
quota. We prove that terminating the group kills a grandchild — i.e. the whole
tree dies, not just the direct child.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from core.engine.comms.enrich.extract import (
    LiveGroups,
    parse_result,
    terminate_group,
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def test_terminate_group_kills_grandchild():
    # Parent (new session leader) spawns a grandchild that sleeps 60s, prints its
    # pid, then waits. Killing the parent's GROUP must take the grandchild too.
    script = (
        "import os,subprocess,sys,time;"
        "c=subprocess.Popen(['sleep','60']);"
        "sys.stdout.write(str(c.pid)+'\\n');sys.stdout.flush();"
        "time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    grandchild_pid = int(proc.stdout.readline().strip())
    assert _alive(grandchild_pid)

    terminate_group(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)

    # Give the OS a moment to reap the grandchild.
    for _ in range(50):
        if not _alive(grandchild_pid):
            break
        time.sleep(0.1)
    assert not _alive(grandchild_pid), "grandchild orphaned — group teardown failed"


def test_live_groups_terminate_all():
    procs = []
    for _ in range(3):
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        procs.append(p)
    live = LiveGroups()
    for p in procs:
        live.register(p.pid)
    killed = live.terminate_all(signal.SIGKILL)
    assert set(killed) == {p.pid for p in procs}
    assert live.shutting_down
    for p in procs:
        p.wait(timeout=5)
        assert not _alive(p.pid)


def test_terminate_group_on_dead_pid_is_safe():
    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait()
    terminate_group(p.pid, signal.SIGKILL)  # must not raise


def test_parse_result_variants():
    assert parse_result('{"entities":[]}') == {"entities": []}
    assert parse_result('```json\n{"entities":[{"type":"topic"}]}\n```')["entities"][0]["type"] == "topic"
    assert parse_result('Sure! {"entities":[]} done')["entities"] == []
    assert parse_result("not json at all") is None
    assert parse_result("") is None
    assert parse_result(None) is None
