"""Tests for People Intelligence nudge generators.

All test inputs are FABRICATED. No real operator data in this file.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta

import pytest

from core.engine.people.intel import nudges
from core.engine.people.intel.nudges import (
    SURFACE_BIRTHDAY,
    SURFACE_DRIFT,
    SURFACE_RECONNECT,
    Nudge,
    gen_birthdays,
    gen_drift,
    gen_reconnect,
    generate_all,
    list_live_nudges,
    mark_actioned,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _bootstrap_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE people (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            is_archived INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE contact_metadata (
            person_id TEXT PRIMARY KEY,
            birthday TEXT,
            birthday_source TEXT
        );
        CREATE TABLE relationship_state (
            person_id TEXT PRIMARY KEY,
            last_interaction_at INTEGER,
            avg_days_between REAL
        );
        CREATE TABLE person_classification (
            person_id TEXT PRIMARY KEY,
            tier TEXT NOT NULL
        );
        CREATE TABLE intelligence_queue (
            id TEXT PRIMARY KEY,
            person_id TEXT REFERENCES people(id),
            surface_type TEXT NOT NULL,
            priority INTEGER DEFAULT 3,
            surface_after INTEGER,
            surfaced_at INTEGER,
            status TEXT DEFAULT 'pending',
            content TEXT,
            context_json TEXT,
            created_at INTEGER,
            expires_at INTEGER
        );
        CREATE UNIQUE INDEX idx_queue_dedup
            ON intelligence_queue(person_id, surface_type, surface_after);
    """)
    return conn


def _add_person(conn, pid, name, archived=False):
    conn.execute(
        "INSERT INTO people (id, canonical_name, is_archived) VALUES (?, ?, ?)",
        (pid, name, 1 if archived else 0),
    )


def _set_birthday(conn, pid, mmdd):
    conn.execute(
        "INSERT INTO contact_metadata (person_id, birthday, birthday_source) "
        "VALUES (?, ?, 'test')",
        (pid, f"0000-{mmdd}"),
    )


def _set_classification(conn, pid, tier):
    conn.execute(
        "INSERT INTO person_classification (person_id, tier) VALUES (?, ?)",
        (pid, tier),
    )


def _set_relstate(conn, pid, last_ts, avg=None):
    conn.execute(
        "INSERT INTO relationship_state (person_id, last_interaction_at, avg_days_between) "
        "VALUES (?, ?, ?)",
        (pid, last_ts, avg),
    )


# ── gen_birthdays ────────────────────────────────────────────────────


def test_birthday_today():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    _add_person(conn, "p_1", "Alice Kumar")
    _set_birthday(conn, "p_1", today.strftime("%m-%d"))
    conn.commit()

    out = gen_birthdays(conn, today=today)
    assert len(out) == 1
    assert out[0].surface_type == SURFACE_BIRTHDAY
    assert "today" in out[0].content.lower()
    assert "Alice Kumar" in out[0].content


def test_birthday_tomorrow():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    tomorrow = today + timedelta(days=1)
    _add_person(conn, "p_1", "Sam Taylor")
    _set_birthday(conn, "p_1", tomorrow.strftime("%m-%d"))
    conn.commit()

    out = gen_birthdays(conn, today=today)
    assert len(out) == 1
    assert "tomorrow" in out[0].content.lower()


def test_birthday_in_seven_days():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    target = today + timedelta(days=7)
    _add_person(conn, "p_1", "Riley Jones")
    _set_birthday(conn, "p_1", target.strftime("%m-%d"))
    conn.commit()

    out = gen_birthdays(conn, today=today)
    assert len(out) == 1
    assert "in 7 days" in out[0].content


def test_birthday_far_future_skipped():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    target = today + timedelta(days=30)
    _add_person(conn, "p_1", "Jordan Lee")
    _set_birthday(conn, "p_1", target.strftime("%m-%d"))
    conn.commit()

    assert gen_birthdays(conn, today=today) == []


def test_birthday_archived_skipped():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    _add_person(conn, "p_1", "Ghost", archived=True)
    _set_birthday(conn, "p_1", today.strftime("%m-%d"))
    conn.commit()
    assert gen_birthdays(conn, today=today) == []


def test_birthday_no_metadata():
    conn = _bootstrap_db()
    _add_person(conn, "p_1", "No Birthday")
    conn.commit()
    assert gen_birthdays(conn, today=date(2026, 6, 15)) == []


def test_birthday_yearless_format_supported():
    conn = _bootstrap_db()
    today = date(2026, 6, 15)
    _add_person(conn, "p_1", "Yearless Person")
    # Real format used by contacts adapter
    conn.execute(
        "INSERT INTO contact_metadata (person_id, birthday) VALUES (?, ?)",
        ("p_1", "0000-06-16"),
    )
    conn.commit()
    out = gen_birthdays(conn, today=today)
    assert len(out) == 1
    assert "tomorrow" in out[0].content.lower()


# ── gen_drift ────────────────────────────────────────────────────────


def test_drift_fires_when_gap_exceeds_2x_avg():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Drifting Friend")
    _set_classification(conn, "p_1", "active")
    # avg=10d, last interaction 25d ago → gap is 2.5x avg → fire
    _set_relstate(conn, "p_1", now - 25 * 86400, avg=10.0)
    conn.commit()

    out = gen_drift(conn, now_ts=now)
    assert len(out) == 1
    assert out[0].surface_type == SURFACE_DRIFT
    assert "Drifting Friend" in out[0].content
    assert "25d" in out[0].content


def test_drift_skips_normal_gap():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Normal")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 15 * 86400, avg=10.0)  # 1.5x → skip
    conn.commit()
    assert gen_drift(conn, now_ts=now) == []


def test_drift_skips_when_avg_unknown():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "No Baseline")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 100 * 86400, avg=None)
    conn.commit()
    assert gen_drift(conn, now_ts=now) == []


def test_drift_skips_dormant_tier():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Dormant")
    _set_classification(conn, "p_1", "dormant")
    _set_relstate(conn, "p_1", now - 100 * 86400, avg=10.0)
    conn.commit()
    assert gen_drift(conn, now_ts=now) == []


def test_drift_skips_archived():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Archived", archived=True)
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 100 * 86400, avg=10.0)
    conn.commit()
    assert gen_drift(conn, now_ts=now) == []


def test_drift_priority_higher_for_core():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_core", "Core Person")
    _set_classification(conn, "p_core", "core")
    _set_relstate(conn, "p_core", now - 30 * 86400, avg=10.0)
    _add_person(conn, "p_act", "Active Person")
    _set_classification(conn, "p_act", "active")
    _set_relstate(conn, "p_act", now - 30 * 86400, avg=10.0)
    conn.commit()

    out = {n.person_id: n.priority for n in gen_drift(conn, now_ts=now)}
    assert out["p_core"] == 1
    assert out["p_act"] == 2


# ── gen_reconnect ────────────────────────────────────────────────────


def test_reconnect_fires_for_70d_silence():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Old Friend")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 70 * 86400)
    conn.commit()

    out = gen_reconnect(conn, now_ts=now)
    assert len(out) == 1
    assert out[0].surface_type == SURFACE_RECONNECT
    assert "70d" in out[0].content


def test_reconnect_skips_recent_contact():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Recent")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 30 * 86400)
    conn.commit()
    assert gen_reconnect(conn, now_ts=now) == []


def test_reconnect_skips_dormant_tier():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Dormant")
    _set_classification(conn, "p_1", "dormant")
    _set_relstate(conn, "p_1", now - 200 * 86400)
    conn.commit()
    assert gen_reconnect(conn, now_ts=now) == []


def test_reconnect_skipped_when_drift_recent():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Drifting")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 70 * 86400, avg=10.0)
    # Pre-seed a drift nudge for this person
    conn.execute(
        "INSERT INTO intelligence_queue (id, person_id, surface_type, surface_after, "
        "status, content, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        ("iq_test", "p_1", SURFACE_DRIFT, now, "drift", now - 86400),
    )
    conn.commit()
    assert gen_reconnect(conn, now_ts=now) == []


# ── generate_all + dedup ────────────────────────────────────────────


def test_generate_all_dedup_on_rerun():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Reconnect Candidate")
    _set_classification(conn, "p_1", "active")
    _set_relstate(conn, "p_1", now - 80 * 86400)
    conn.commit()

    first = generate_all(conn)
    second = generate_all(conn)
    assert first[SURFACE_RECONNECT] == 1
    assert second[SURFACE_RECONNECT] == 0

    n_rows = conn.execute(
        "SELECT COUNT(*) FROM intelligence_queue WHERE surface_type = ?",
        (SURFACE_RECONNECT,),
    ).fetchone()[0]
    assert n_rows == 1


def test_generate_all_runs_all_three_generators():
    conn = _bootstrap_db()
    now = int(time.time())
    today = date.fromtimestamp(now)

    # Birthday tomorrow
    _add_person(conn, "p_b", "BdayPerson")
    _set_birthday(conn, "p_b", (today + timedelta(days=1)).strftime("%m-%d"))

    # Drift candidate
    _add_person(conn, "p_d", "DriftPerson")
    _set_classification(conn, "p_d", "active")
    _set_relstate(conn, "p_d", now - 30 * 86400, avg=5.0)

    # Reconnect candidate (different person — drift would otherwise win)
    _add_person(conn, "p_r", "ReconnectPerson")
    _set_classification(conn, "p_r", "active")
    _set_relstate(conn, "p_r", now - 80 * 86400)

    conn.commit()
    counts = generate_all(conn)
    assert counts[SURFACE_BIRTHDAY] == 1
    assert counts[SURFACE_DRIFT] == 1
    assert counts[SURFACE_RECONNECT] == 1


# ── list_live_nudges + mark_actioned ─────────────────────────────────


def test_list_live_nudges_filters_by_window():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "Live")
    _add_person(conn, "p_2", "Future")
    _add_person(conn, "p_3", "Expired")

    conn.executemany(
        "INSERT INTO intelligence_queue (id, person_id, surface_type, priority, "
        "surface_after, expires_at, status, content, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        [
            ("iq_a", "p_1", SURFACE_RECONNECT, 2, now - 100, now + 1000, "live", now),
            ("iq_b", "p_2", SURFACE_RECONNECT, 2, now + 1000, now + 2000, "future", now),
            ("iq_c", "p_3", SURFACE_RECONNECT, 2, now - 1000, now - 100, "expired", now),
        ],
    )
    conn.commit()

    live = list_live_nudges(conn, now_ts=now)
    ids = [n["id"] for n in live]
    assert ids == ["iq_a"]
    assert live[0]["name"] == "Live"


def test_mark_actioned_updates_status():
    conn = _bootstrap_db()
    now = int(time.time())
    _add_person(conn, "p_1", "X")
    conn.execute(
        "INSERT INTO intelligence_queue (id, person_id, surface_type, surface_after, "
        "status, content, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        ("iq_x", "p_1", SURFACE_RECONNECT, now - 100, "test", now),
    )
    conn.commit()

    assert mark_actioned(conn, "iq_x") is True
    row = conn.execute(
        "SELECT status FROM intelligence_queue WHERE id = ?", ("iq_x",)
    ).fetchone()
    assert row[0] == "acted"
    # Idempotent: re-marking returns False
    assert mark_actioned(conn, "iq_x") is False
