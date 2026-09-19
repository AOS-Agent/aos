"""Migration 112 — booting an arm out must outlast launchd's asynchrony.

2026-09-19: a friend machine updating v0.7.1 → v0.7.13 halted at migration 112
with `✗ Failed` after printing a ✓ for all three arms. `launchctl bootout`
returns before launchd has finished tearing a job down, and each of these arms
ships `KeepAlive: true`, so the domain read that `up()` ends with (`check()`)
still saw `com.aos.sentinel` and reported failure — on a machine where the
migration had in fact just succeeded. A failed migration halts the chain
behind it, so 24 further migrations never ran and the machine sat mid-upgrade.

The wait therefore belongs in `_bootout`, where the asynchrony is. These
tests pin that: survive the race, stay honest about a job that truly will not
leave, and never block when there is nothing to wait for.
"""
from __future__ import annotations

import importlib.util
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "core" / "infra" / "migrations" / "112_comms_arms_default_off.py"


@pytest.fixture()
def m(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    spec = importlib.util.spec_from_file_location("m112", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Never actually shell out to launchctl from a test.
    mod.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    )
    return mod


def test_survives_the_keepalive_race(m):
    """The reference failure: the job is still visible for a moment, then goes."""
    t0 = time.monotonic()
    m._loaded = lambda label: (time.monotonic() - t0) < 0.5

    assert m._bootout("com.aos.sentinel", settle=5.0) is True, (
        "a job that leaves shortly after bootout must read as booted out — "
        "reporting failure here is what halted the migration chain"
    )


def test_reports_a_job_that_never_leaves(m):
    """Honesty in the other direction: don't claim success, don't hang."""
    m._loaded = lambda label: True

    started = time.monotonic()
    result = m._bootout("com.aos.sentinel", settle=0.5)
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 5.0, "must give up at the settle deadline, not block the update"


def test_returns_immediately_when_already_gone(m):
    m._loaded = lambda label: False

    started = time.monotonic()
    assert m._bootout("com.aos.converse", settle=5.0) is True
    assert time.monotonic() - started < 0.5, "nothing to wait for"


def test_up_names_the_holdout_instead_of_printing_a_tick(m, capsys):
    """The original printed ✓ for every arm and then failed, leaving no trace
    of which one was the holdout."""
    m.disable_service = lambda name: True
    m.is_opted_in = lambda name: False
    m.needs_disabling = lambda arms: False
    m._loaded = lambda label: True  # nothing ever leaves

    # Same code path, without paying the real 10s-per-arm settle in a unit test.
    real_bootout = m._bootout
    m._bootout = lambda label, settle=0.2: real_bootout(label, settle=settle)

    assert m.up() is False
    out = capsys.readouterr().out
    assert "STILL LOADED" in out
    assert "com.aos.sentinel" in out
