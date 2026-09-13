"""Quiet hours: queue the non-urgent, never drop it (aos#235).

The 2026-09-13 review: "Quiet hours: **do not exist today** — `operator.yaml`
has `schedule.blocks` and `daily_loop` times but no `notifications.quiet_hours`
key, and the notify router has no time-of-day suppression at all."

The operator's call: non-urgent outbound queues during quiet hours and flushes
as one digest at the end of them; urgent still sends. Urgency is decided by the
sender's own declaration (`kind="alert"`, or an explicit `urgent=True`), never by
reading the text.

The window is derived from what `operator.yaml` already says rather than
demanding a new key: the day ends at the evening check-in and starts at the
morning briefing. An explicit `notifications.quiet_hours` wins if present;
22:00-07:00 is the fallback when the file says nothing.

The clock is frozen by monkeypatching `router._now` — no freezegun in the core
environment, and a parameter the production path also uses is a better seam
anyway.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROUTER_PATH = REPO / "core" / "engine" / "notify" / "router.py"


def _load_router():
    spec = importlib.util.spec_from_file_location("router_quiet_under_test", ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A router with a throwaway store, no real Telegram, and a frozen clock."""
    router = _load_router()
    db = tmp_path / "bridge.db"
    monkeypatch.setenv("AOS_BRIDGE_DB", str(db))

    sent: list[str] = []
    monkeypatch.setattr(router, "_get_secret",
                        lambda name: "token" if name == "TELEGRAM_BOT_TOKEN" else "999")
    monkeypatch.setattr(router, "_load_topics", lambda: (None, {}))
    monkeypatch.setattr(router, "TOPICS_CONFIG", tmp_path / "bridge-topics.yaml")

    def fake_send(token, chat_id, text, *a, **kw):
        sent.append(text)
        return True, ""

    monkeypatch.setattr(router, "_send_with_retry", fake_send)

    # No operator.yaml by default -> the 22:00-07:00 fallback.
    op = tmp_path / "operator.yaml"
    monkeypatch.setattr(router, "OPERATOR_CONFIG", op)

    def at(hh: int, mm: int = 0):
        monkeypatch.setattr(router, "_now", lambda: datetime(2026, 9, 13, hh, mm))

    return {"router": router, "sent": sent, "db": db, "operator": op, "at": at,
            "tmp": tmp_path}


def _queued(db: Path) -> list[tuple]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT text_redacted, status FROM messages WHERE status = 'queued' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _all_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT text_redacted, status FROM messages ORDER BY id").fetchall()
    finally:
        conn.close()


# ── The window ──────────────────────────────────────────────────────────────

def test_default_window_is_22_to_07(env):
    r = env["router"]
    assert r.quiet_window() == ((22, 0), (7, 0))


def test_explicit_notifications_key_wins(env):
    env["operator"].write_text(
        "notifications:\n  quiet_hours:\n    start: '23:30'\n    end: '06:15'\n")
    assert env["router"].quiet_window() == ((23, 30), (6, 15))


def test_falls_back_to_the_operators_own_day_boundaries(env):
    """No new key needed: the day ends at the check-in, starts at the briefing."""
    env["operator"].write_text(
        "daily_loop:\n  morning_briefing: 06:00\n  evening_checkin: '21:00'\n")
    assert env["router"].quiet_window() == ((21, 0), (6, 0))


def test_quiet_hours_can_be_switched_off(env):
    env["operator"].write_text("notifications:\n  quiet_hours:\n    enabled: false\n")
    assert env["router"].quiet_window() is None
    assert env["router"].in_quiet_hours(datetime(2026, 9, 13, 2, 0)) is False


@pytest.mark.parametrize("hh,expected", [
    (22, True), (23, True), (0, True), (2, True), (6, True),
    (7, False), (9, False), (12, False), (21, False),
])
def test_window_spans_midnight(env, hh, expected):
    assert env["router"].in_quiet_hours(datetime(2026, 9, 13, hh, 30 if hh != 7 else 0)) is expected


# ── Urgency is declared, never inferred from the text ───────────────────────

@pytest.mark.parametrize("kind,urgent", [
    ("alert", True), ("info", False), ("success", False),
])
def test_urgency_comes_from_the_sender_kind(env, kind, urgent):
    assert env["router"].is_urgent(kind) is urgent


def test_a_sender_can_force_urgent(env):
    assert env["router"].is_urgent("info", urgent=True) is True


def test_a_sender_can_force_non_urgent(env):
    assert env["router"].is_urgent("alert", urgent=False) is False


# ── The done-when ───────────────────────────────────────────────────────────

def test_non_urgent_inside_quiet_hours_is_queued_not_sent(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)

    result = r.send_notification("Seven old scripts are still lying around.", kind="info")

    assert sent == [], "a non-urgent message went out during quiet hours"
    assert result["delivered"] is False
    assert result["queued"] is True
    rows = _queued(env["db"])
    assert len(rows) == 1
    assert "old scripts" in rows[0][0]


def test_urgent_inside_quiet_hours_still_goes_out(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)

    result = r.send_notification("The transcriber stopped.", kind="alert")

    assert len(sent) == 1
    assert "transcriber stopped" in sent[0]
    assert result["delivered"] is True
    assert _queued(env["db"]) == []


def test_flush_at_seven_emits_exactly_one_digest_with_both_items(env):
    r, sent = env["router"], env["sent"]

    env["at"](2, 0)
    r.send_notification("Seven old scripts are still lying around.", kind="info")
    r.send_notification("The weekly summary is ready.", kind="info")
    assert sent == []
    assert len(_queued(env["db"])) == 2

    env["at"](7, 0)
    result = r.flush_quiet_queue()

    assert len(sent) == 1, f"expected one digest, got {len(sent)}: {sent}"
    digest = sent[0]
    assert "old scripts" in digest
    assert "weekly summary" in digest
    assert result["flushed"] == 2
    assert _queued(env["db"]) == []
    assert all(status == "digested" or status == "sent"
               for _, status in _all_rows(env["db"]))


def test_flush_is_a_noop_during_quiet_hours(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    r.send_notification("Seven old scripts.", kind="info")

    result = r.flush_quiet_queue()

    assert sent == []
    assert result["flushed"] == 0
    assert len(_queued(env["db"])) == 1


def test_flush_with_an_empty_queue_sends_nothing(env):
    r, sent = env["router"], env["sent"]
    env["at"](9, 0)
    result = r.flush_quiet_queue()
    assert sent == []
    assert result["flushed"] == 0


def test_flush_does_not_resend_an_already_digested_item(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    r.send_notification("Seven old scripts.", kind="info")
    env["at"](7, 0)
    r.flush_quiet_queue()
    assert len(sent) == 1
    r.flush_quiet_queue()
    assert len(sent) == 1, "the digest was sent twice"


def test_non_urgent_outside_quiet_hours_sends_immediately(env):
    r, sent = env["router"], env["sent"]
    env["at"](10, 0)
    result = r.send_notification("The weekly summary is ready.", kind="info")
    assert len(sent) == 1
    assert result["delivered"] is True
    assert _queued(env["db"]) == []


def test_the_digest_is_humanized_like_any_other_message(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    r.send_notification("A cron died reading ~/.aos/config/crons.yaml", kind="info")
    env["at"](7, 0)
    r.flush_quiet_queue()
    assert "crons.yaml" not in sent[0]
    assert "A cron died reading" in sent[0]


def test_the_digest_caps_its_items(env):
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    for i in range(9):
        r.send_notification(f"Thing number {i} happened.", kind="info")
    env["at"](7, 0)
    result = r.flush_quiet_queue()
    assert len(sent) == 1
    assert "more" in sent[0]
    assert result["flushed"] == 9, "every queued row must be cleared, shown or not"


def test_a_sender_can_opt_out_of_queueing(env):
    """Something time-critical but not an alert still has a way through."""
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    r.send_notification("Your flight leaves in an hour.", kind="info", urgent=True)
    assert len(sent) == 1


def test_queueing_never_silently_drops_when_the_store_is_unwritable(env, monkeypatch):
    """A queue that can't queue must send, not swallow."""
    r, sent = env["router"], env["sent"]
    env["at"](2, 0)
    monkeypatch.setattr(r, "_queue_for_digest", lambda *a, **kw: None)
    result = r.send_notification("Seven old scripts.", kind="info")
    assert len(sent) == 1, "a message vanished when the queue failed"
    assert result["delivered"] is True


# ── The scheduler can call it ───────────────────────────────────────────────

def test_a_cron_entry_exists_for_the_flush():
    crons = (REPO / "config" / "crons.yaml").read_text()
    assert "notify-flush" in crons, "nothing calls flush_quiet_queue on a schedule"


def test_the_flush_script_exists_and_is_executable():
    script = REPO / "core" / "bin" / "crons" / "notify-flush"
    assert script.exists(), "config/crons.yaml points at a script that is not there"
    assert script.stat().st_mode & 0o111, "notify-flush is not executable"
