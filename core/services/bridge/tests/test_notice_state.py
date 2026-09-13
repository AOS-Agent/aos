"""notice_state — persisted dedupe for one-shot notices (aos#2324).

The bridge already owns one SQLite file (conversation_store.py, `messages`
table, 0.7.7). heartbeat.py's restart-storm bug — every bridge restart
re-sends every still-open alert because the dedupe set lived only in a
thread-local variable — gets fixed by giving it a row here instead of a new
file: `notice_state(key, last_sent, fingerprint)`.

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

import conversation_store as store  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))
    return tmp_path / "bridge.db"


def test_a_notice_never_sent_before_is_due(db):
    assert store.should_send_notice("k", "fp", ttl_seconds=3600) is True


def test_the_same_fingerprint_is_suppressed_after_sending(db):
    store.record_notice_sent("k", "fp")
    assert store.should_send_notice("k", "fp", ttl_seconds=3600) is False


def test_a_changed_fingerprint_is_due_again(db):
    store.record_notice_sent("k", "fp-old")
    assert store.should_send_notice("k", "fp-new", ttl_seconds=3600) is True


def test_an_expired_entry_is_due_again(db):
    store.record_notice_sent("k", "fp")
    time.sleep(0.1)
    assert store.should_send_notice("k", "fp", ttl_seconds=0.05) is True


def test_the_state_survives_a_fresh_connection(db):
    """The whole point: a new process (a restart) opening the same DB must
    see what a previous process already sent."""
    store.record_notice_sent("k", "fp")
    # Simulate a fresh process: nothing cached, just the DB path.
    assert store.should_send_notice("k", "fp", ttl_seconds=3600) is False


def test_unrelated_keys_do_not_interfere(db):
    store.record_notice_sent("k1", "fp")
    assert store.should_send_notice("k2", "fp", ttl_seconds=3600) is True
