"""
Issue #2356 — the scheduler's check_reboot() sent a false "System rebooted"
Telegram notification on essentially every tick, forever, because dedupe was
an exact string match on `kern.boottime`'s reported second: NTP clock slew
oscillates that value across a second boundary, so consecutive ticks alternate
:35 / :36 / :35 / :36 forever, each one reading as "a new boot" (operator log:
~/.aos/logs/uptime.log, REBOOT detected on essentially every 25-minute tick for
days, boot alternating between two adjacent seconds).

Fix (core/bin/internal/scheduler, check_reboot()):
  - dedupe tolerates slew: the same boot if the two boottimes are within
    BOOT_TOLERANCE_SECONDS of each other, not an exact string match.
  - persisted state is the raw integer boot second, not an ISO string — a
    legacy ISO-string value already on disk is treated as "no prior boot on
    record" (never a crash), and converges onto the new format on this tick.
  - the "uptime < 10 minutes" guard the docstring and core/agents/steward.md
    already promised is now actually enforced: a "new" boot older than
    REBOOT_FRESH_WINDOW_SECONDS is adopted (state written so it isn't
    recomputed forever) but never announced as having "just happened". This
    is the fix for a first-ever run, or a stale/missing state file, landing
    long after the actual boot — decision documented in check_reboot()'s own
    docstring: silently record, never spam.

These tests stub `_send_telegram` directly (never a real aos-notify/Telegram
call) and fully sandbox `subprocess.run` so check_reboot() can never shell out
to a real `sysctl`/`launchctl` on the host running the suite.
"""
from __future__ import annotations

import importlib.util
import subprocess
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCHEDULER_PATH = REPO / "core" / "bin" / "internal" / "scheduler"


def _mod():
    loader = SourceFileLoader("scheduler_under_test", str(SCHEDULER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sched = _mod()


def _boottime_stdout(boot_sec: int) -> str:
    return f"{{ sec = {boot_sec}, usec = 0 }} Thu Jan  1 00:00:00 2026\n"


class _FakeRun:
    """Stand-in for subprocess.run inside check_reboot(): answers sysctl and
    launchctl, raises on anything else so a regression can never shell out
    for real — this must never restart a real LaunchAgent or read the real
    host's boot time."""

    def __init__(self, boot_sec: int):
        self.boot_sec = boot_sec

    def __call__(self, cmd, **kwargs):
        if cmd[0] == "/usr/sbin/sysctl":
            return subprocess.CompletedProcess(cmd, 0, stdout=_boottime_stdout(self.boot_sec), stderr="")
        if cmd[0] == "launchctl":
            return subprocess.CompletedProcess(cmd, 0, stdout="com.aos.bridge\n", stderr="")
        raise AssertionError(f"check_reboot() must not shell out to {cmd!r}")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sched, "HOME", tmp_path)
    monkeypatch.setattr(sched, "LAST_BOOT_FILE", tmp_path / "last-boot")
    monkeypatch.setattr(sched, "UPTIME_LOG", tmp_path / "uptime.log")
    sent = []
    # The one function that would shell out to aos-notify (-> Telegram). Stub
    # it directly rather than mocking a subprocess call, so it is never even
    # theoretically possible for this suite to send a real notification.
    monkeypatch.setattr(
        sched, "_send_telegram",
        lambda msg, topic="alerts": sent.append((msg, topic)) or True,
    )
    return tmp_path, sent


def _set_boottime(monkeypatch, boot_sec: int) -> _FakeRun:
    fake = _FakeRun(boot_sec)
    monkeypatch.setattr(sched.subprocess, "run", fake)
    return fake


# ── same boot, clock slew ────────────────────────────────────────────────

def test_boottime_drifting_a_few_seconds_across_ticks_notifies_once(sandbox, monkeypatch):
    _, sent = sandbox
    now = int(time.time())
    boot_a = now - 30  # "just booted" 30s ago — within the fresh window

    _set_boottime(monkeypatch, boot_a)
    sched.check_reboot()
    assert len(sent) == 1

    # Next tick: NTP slews the reported second by 3s. Same boot.
    _set_boottime(monkeypatch, boot_a + 3)
    sched.check_reboot()
    assert len(sent) == 1, "clock slew within tolerance must not re-notify"

    # Slew the other way — still the same boot.
    _set_boottime(monkeypatch, boot_a - 2)
    sched.check_reboot()
    assert len(sent) == 1

    # The exact reproduction from the issue: alternating +/-1s forever.
    for _ in range(6):
        boot_a = boot_a + 1 if boot_a % 2 else boot_a - 1
        _set_boottime(monkeypatch, boot_a)
        sched.check_reboot()
    assert len(sent) == 1, "ping-ponging boottime must still read as one boot"


def test_boottime_two_hours_later_is_a_new_boot_and_notifies_again(sandbox, monkeypatch):
    _, sent = sandbox
    now = int(time.time())
    boot_a = now - 30

    _set_boottime(monkeypatch, boot_a)
    sched.check_reboot()
    assert len(sent) == 1

    # A real reboot two hours later — freshly booted again (uptime ~30s).
    boot_b = boot_a + 7200
    monkeypatch.setattr(sched.time, "time", lambda: float(boot_b + 30))
    _set_boottime(monkeypatch, boot_b)
    sched.check_reboot()
    assert len(sent) == 2, "a genuinely new boot 2h later must notify"


# ── first-ever run / stale state must not spam ──────────────────────────

def test_first_run_long_after_actual_boot_does_not_notify(sandbox, monkeypatch):
    """No state file yet (fresh install) and the machine has been up for
    days: the documented uptime<10min guard means this records the boot
    silently instead of announcing a reboot that happened days ago."""
    tmp_path, sent = sandbox
    boot_sec = int(time.time()) - 4 * 24 * 3600  # booted 4 days ago

    _set_boottime(monkeypatch, boot_sec)
    sched.check_reboot()

    assert sent == []
    # Persisted anyway, so this isn't recomputed (and re-logged) every tick.
    assert sched.LAST_BOOT_FILE.exists()
    assert sched.LAST_BOOT_FILE.read_text().strip() == str(boot_sec)


def test_first_run_shortly_after_actual_boot_does_notify(sandbox, monkeypatch):
    """Symmetric case: if the very first tick genuinely lands within the
    fresh window, it is indistinguishable from any other new boot and is
    reported the same way — 'first run' alone is not a suppression rule,
    only staleness is."""
    _, sent = sandbox
    boot_sec = int(time.time()) - 45
    _set_boottime(monkeypatch, boot_sec)
    sched.check_reboot()
    assert len(sent) == 1


def test_legacy_iso_string_state_file_converges_without_crashing(sandbox, monkeypatch):
    """A machine mid-upgrade still has the pre-fix ISO-string format on disk.
    It must not crash, and must converge onto the new integer format."""
    tmp_path, sent = sandbox
    sched.LAST_BOOT_FILE.write_text("2026-09-01T00:00:00")
    boot_sec = int(time.time()) - 4 * 24 * 3600  # old boot, stale — no notify

    _set_boottime(monkeypatch, boot_sec)
    sched.check_reboot()

    assert sent == []
    assert sched.LAST_BOOT_FILE.read_text().strip() == str(boot_sec)


def test_a_directory_at_last_boot_file_is_handled_gracefully(sandbox, monkeypatch):
    """The documented operator-side mitigation for #2356 replaced .last-boot
    with a directory to force an exception before the notify. The fixed
    code must not depend on that workaround to avoid spamming, but on a
    machine that hasn't been migrated yet (see migration 133) it must still
    degrade quietly rather than crash the scheduler tick."""
    tmp_path, sent = sandbox
    sched.LAST_BOOT_FILE.mkdir(parents=True)
    boot_sec = int(time.time()) - 30

    _set_boottime(monkeypatch, boot_sec)
    sched.check_reboot()  # must not raise

    assert sent == []  # caught by the outer except — logged, not crashed
