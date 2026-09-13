"""Heartbeat dedupe survives a restart (aos#2324).

`last_reported` used to be a plain local inside `start_heartbeat()._loop()` —
it died with the thread, so every bridge restart re-sent every still-open
problem (3,234 restarts logged, each one re-sending the same "whatsmeow has
stopped" alert). `Heartbeat` now consults `notice_state` (conversation_store.py)
before sending, so a fresh instance — standing in for a fresh process after a
restart — knows what a previous one already reported.

Runs in the bridge venv:
    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import heartbeat as hb  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))


@pytest.fixture
def sent(monkeypatch):
    """Nothing in this suite may reach Telegram or the live bridge — `_alert`
    is the one function that would, so it's replaced everywhere."""
    out: list[str] = []

    def fake_alert(bot_token, chat_id, msg):
        out.append(msg)

    monkeypatch.setattr(hb, "_alert", fake_alert)
    return out


def _fresh(problems, monkeypatch, ttl_seconds=None):
    """A brand-new Heartbeat — as if the bridge had just been restarted.

    No shared in-memory state with any other instance in the test; the only
    thing two instances share is the DB path (via AOS_BRIDGE_DB).
    """
    monkeypatch.setattr(hb, "_is_active_hours", lambda: True)
    monkeypatch.setattr(hb, "_check_health", lambda token: {})
    monkeypatch.setattr(hb, "_find_problems", lambda health: list(problems))
    h = hb.Heartbeat("token", 999)
    if ttl_seconds is not None:
        h.ttl_seconds = ttl_seconds
    return h


def test_same_alert_across_two_fresh_instances_sends_once(db, monkeypatch, sent):
    problems = ["🔴 whatsmeow has stopped."]

    _fresh(problems, monkeypatch).run_once()
    _fresh(problems, monkeypatch).run_once()  # a second restart, same problem

    assert sent == ["🔴 whatsmeow has stopped."]


def test_a_changed_alert_sends_again(db, monkeypatch, sent):
    _fresh(["🔴 whatsmeow has stopped."], monkeypatch).run_once()
    _fresh(["🔴 qareen has stopped."], monkeypatch).run_once()

    assert sent == ["🔴 whatsmeow has stopped.", "🔴 qareen has stopped."]


def test_after_expiry_it_sends_again(db, monkeypatch, sent):
    problems = ["🔴 whatsmeow has stopped."]

    _fresh(problems, monkeypatch, ttl_seconds=0.05).run_once()
    time.sleep(0.1)
    _fresh(problems, monkeypatch, ttl_seconds=0.05).run_once()

    assert sent == problems * 2


def test_quiet_hours_send_nothing_and_do_not_consume_dedupe(db, monkeypatch, sent):
    monkeypatch.setattr(hb, "_is_active_hours", lambda: False)
    monkeypatch.setattr(hb, "_check_health", lambda token: {})
    monkeypatch.setattr(hb, "_find_problems", lambda health: ["🔴 x has stopped."])
    h = hb.Heartbeat("token", 999)
    h.run_once()
    assert sent == []


def test_a_recovered_problem_within_one_run_resets_in_memory_state(db, monkeypatch, sent):
    """Within a single running process, all-clear still resets the in-memory
    tracker so an immediate recurrence is treated the same as the first sighting
    for dedupe purposes — the persisted layer is what caps repeats over time."""
    monkeypatch.setattr(hb, "_is_active_hours", lambda: True)
    monkeypatch.setattr(hb, "_check_health", lambda token: {})
    problems = ["🔴 whatsmeow has stopped."]

    calls = {"problems": problems}

    def fake_find(health):
        return calls["problems"]

    monkeypatch.setattr(hb, "_find_problems", fake_find)
    h = hb.Heartbeat("token", 999)
    h.run_once()
    assert sent == ["🔴 whatsmeow has stopped."]

    calls["problems"] = []
    h.run_once()  # all clear
    assert h.last_reported == set()
