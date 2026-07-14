"""Tests for the Call History signal adapter (phone + FaceTime).

Builds a tiny fake CallHistory.storedata with a minimal ZCALLRECORD
table containing hand-crafted rows, then verifies that the adapter
produces the correct per-person VoiceSignal aggregates.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.calls import (
    CALL_TYPE_FACETIME_AUDIO,
    CALL_TYPE_FACETIME_VIDEO,
    CALL_TYPE_PHONE,
    CORE_DATA_EPOCH_OFFSET,
    CallHistoryAdapter,
)
from core.engine.people.intel.types import PersonSignals, SignalType


# ── fixture helpers ───────────────────────────────────────────────────


def _unix_to_core_data(unix_ts: float) -> float:
    return unix_ts - CORE_DATA_EPOCH_OFFSET


def _iso_to_core_data(iso: str) -> float:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _unix_to_core_data(dt.timestamp())


def _make_fixture_db(path: Path, rows: list[dict]) -> None:
    """Create a minimal ZCALLRECORD table at `path` with the given rows.

    Only the columns queried by the adapter are created.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE ZCALLRECORD (
                Z_PK         INTEGER PRIMARY KEY AUTOINCREMENT,
                ZDATE        REAL,
                ZDURATION    REAL,
                ZADDRESS     BLOB,
                ZORIGINATED  INTEGER,
                ZANSWERED    INTEGER,
                ZCALLTYPE    INTEGER
            )
            """
        )
        for r in rows:
            conn.execute(
                """
                INSERT INTO ZCALLRECORD
                    (ZDATE, ZDURATION, ZADDRESS, ZORIGINATED, ZANSWERED, ZCALLTYPE)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    r["z_date"],
                    r["z_duration"],
                    r["z_address"],
                    r["z_originated"],
                    r["z_answered"],
                    r["z_calltype"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


# Two timestamps in different YYYY-MM buckets.
_T_JAN = _iso_to_core_data("2026-01-15T14:00:00+00:00")  # 2026-01
_T_FEB = _iso_to_core_data("2026-02-20T21:30:00+00:00")  # 2026-02

# Alice's phone (country code + US number). Matching is on last 10 digits.
ALICE_PHONE_RAW = "+1 (415) 555-0100"
ALICE_PHONE_DIFF_FORMAT = "14155550100"       # same number, different format
ALICE_EMAIL = "alice@example.com"

UNKNOWN_PHONE = "+14155559999"


@pytest.fixture()
def person_index():
    return {
        "p_alice": {
            "name": "Alice Example",
            "phones": [ALICE_PHONE_RAW],
            "emails": [ALICE_EMAIL],
            "wa_jids": [],
        },
    }


@pytest.fixture()
def fixture_rows():
    return [
        # 1. Outgoing phone call, answered, 120 sec, Jan.
        {
            "z_date": _T_JAN,
            "z_duration": 120.0,
            "z_address": ALICE_PHONE_DIFF_FORMAT.encode("utf-8"),
            "z_originated": 1,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_PHONE,
        },
        # 2. Incoming phone call, answered, 60 sec, Jan.
        {
            "z_date": _T_JAN + 3600,
            "z_duration": 60.0,
            "z_address": ALICE_PHONE_RAW.encode("utf-8"),
            "z_originated": 0,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_PHONE,
        },
        # 3. Incoming phone call, missed, 0 sec, Jan.
        {
            "z_date": _T_JAN + 7200,
            "z_duration": 0.0,
            "z_address": ALICE_PHONE_RAW.encode("utf-8"),
            "z_originated": 0,
            "z_answered": 0,
            "z_calltype": CALL_TYPE_PHONE,
        },
        # 4. FaceTime audio outgoing, 300 sec, Feb (email address path).
        {
            "z_date": _T_FEB,
            "z_duration": 300.0,
            "z_address": ALICE_EMAIL.encode("utf-8"),
            "z_originated": 1,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_FACETIME_AUDIO,
        },
        # 5. FaceTime video outgoing, 600 sec, Feb.
        {
            "z_date": _T_FEB + 3600,
            "z_duration": 600.0,
            "z_address": ALICE_PHONE_DIFF_FORMAT.encode("utf-8"),
            "z_originated": 1,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_FACETIME_VIDEO,
        },
        # 6. Unmatched address — should be skipped.
        {
            "z_date": _T_FEB + 7200,
            "z_duration": 42.0,
            "z_address": UNKNOWN_PHONE.encode("utf-8"),
            "z_originated": 1,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_PHONE,
        },
    ]


@pytest.fixture()
def fixture_db(tmp_path, fixture_rows):
    db_path = tmp_path / "CallHistory.storedata"
    _make_fixture_db(db_path, fixture_rows)
    return db_path


@pytest.fixture()
def adapter(fixture_db):
    return CallHistoryAdapter(db_path=fixture_db)


# ── tests ─────────────────────────────────────────────────────────────


def test_is_available_true_false(tmp_path, fixture_db):
    # Present & readable.
    a = CallHistoryAdapter(db_path=fixture_db)
    assert a.is_available() is True

    # Missing file.
    missing = tmp_path / "does-not-exist.storedata"
    b = CallHistoryAdapter(db_path=missing)
    assert b.is_available() is False


def test_extract_returns_dict(adapter, person_index):
    result = adapter.extract_all(person_index)
    assert isinstance(result, dict)
    assert "p_alice" in result
    ps = result["p_alice"]
    assert isinstance(ps, PersonSignals)
    assert ps.person_id == "p_alice"
    assert ps.person_name == "Alice Example"
    assert ps.source_coverage == ["calls"]
    assert len(ps.voice) == 1
    assert ps.voice[0].source == "calls"


def test_total_and_answered_counts(adapter, person_index):
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    # 5 matched records (the 6th was unmatched).
    assert vs.total_calls == 5
    # Answered: #1, #2, #4, #5 = 4. Missed (#3).
    assert vs.answered_calls == 4
    assert vs.missed_calls == 1
    # Outgoing: #1, #4, #5 = 3. Incoming: #2, #3 = 2.
    assert vs.outgoing == 3
    assert vs.incoming == 2


def test_duration_aggregates(adapter, person_index):
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    # Total seconds: 120 + 60 + 0 + 300 + 600 = 1080 → 18 minutes.
    assert vs.total_minutes == pytest.approx(18.0)
    # Avg: 1080 / 5 / 60 = 3.6 minutes.
    assert vs.avg_duration_minutes == pytest.approx(3.6)
    # Max: 600 sec = 10 minutes.
    assert vs.max_duration_minutes == pytest.approx(10.0)


def test_call_type_breakdown(adapter, person_index):
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    assert vs.phone_calls == 3
    assert vs.facetime_audio == 1
    assert vs.facetime_video == 1


def test_answer_rate_computed(adapter, person_index):
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    assert vs.answer_rate == pytest.approx(4 / 5)


def test_temporal_buckets_populated(adapter, person_index):
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    # Two distinct YYYY-MM buckets.
    assert set(vs.temporal_buckets.keys()) == {"2026-01", "2026-02"}
    assert vs.temporal_buckets["2026-01"] == 3
    assert vs.temporal_buckets["2026-02"] == 2
    # Dates exported as ISO strings.
    assert vs.first_call_date is not None
    assert vs.last_call_date is not None
    assert vs.first_call_date.startswith("2026-01")
    assert vs.last_call_date.startswith("2026-02")
    # Pattern should not be "none".
    assert vs.temporal_pattern != "none"


def test_phone_suffix_matching(tmp_path, fixture_rows):
    """A person whose phone has a country code still matches records
    that store only the bare 10-digit form."""
    db_path = tmp_path / "CallHistory.storedata"
    _make_fixture_db(db_path, fixture_rows)
    adapter = CallHistoryAdapter(db_path=db_path)

    # Person's stored phone: with country code and formatting.
    # Matched records store the raw 10 digits.
    person_index = {
        "p_alice": {
            "name": "Alice",
            "phones": ["+1-415-555-0100"],
            "emails": [],
            "wa_jids": [],
        }
    }
    result = adapter.extract_all(person_index)
    assert "p_alice" in result
    vs = result["p_alice"].voice[0]
    # Email-addressed call (#4) should NOT match now (no email in index).
    # Matches: #1 (digit form), #2 (raw+format), #3 (raw+format), #5 (digit form) = 4.
    assert vs.total_calls == 4
    assert vs.facetime_audio == 0  # the email-matched FaceTime is gone
    assert vs.facetime_video == 1


def test_email_matching_path(tmp_path):
    """A FaceTime call whose ZADDRESS is an email matches on the
    email_to_pid lookup path."""
    db_path = tmp_path / "CallHistory.storedata"
    rows = [
        {
            "z_date": _T_FEB,
            "z_duration": 300.0,
            "z_address": b"bob@example.com",
            "z_originated": 1,
            "z_answered": 1,
            "z_calltype": CALL_TYPE_FACETIME_AUDIO,
        }
    ]
    _make_fixture_db(db_path, rows)
    adapter = CallHistoryAdapter(db_path=db_path)

    person_index = {
        "p_bob": {
            "name": "Bob",
            "phones": [],
            "emails": ["Bob@Example.COM"],  # case-insensitive
            "wa_jids": [],
        }
    }
    result = adapter.extract_all(person_index)
    assert "p_bob" in result
    vs = result["p_bob"].voice[0]
    assert vs.total_calls == 1
    assert vs.facetime_audio == 1


def test_unmatched_call_skipped(adapter, person_index):
    """The fixture has a row with an unknown phone number that
    doesn't map to anyone — it must NOT inflate Alice's counts."""
    vs = adapter.extract_all(person_index)["p_alice"].voice[0]
    # 5 matched records. The 6th (UNKNOWN_PHONE) is excluded.
    assert vs.total_calls == 5
    # Total minutes excludes the unmatched 42 seconds.
    assert vs.total_minutes == pytest.approx(18.0)
    # And the result dict has only Alice.
    result = adapter.extract_all(person_index)
    assert set(result.keys()) == {"p_alice"}


def test_adapter_class_attributes():
    """Frozen contract: name / display_name / platform / signal_types."""
    assert CallHistoryAdapter.name == "calls"
    assert CallHistoryAdapter.display_name == "Call History"
    assert CallHistoryAdapter.platform == "macos"
    assert CallHistoryAdapter.signal_types == [SignalType.VOICE]
    assert "CallHistoryDB" in " ".join(CallHistoryAdapter.requires)


def test_extract_with_no_matches_returns_empty(adapter):
    """Empty person_index → empty result dict, no crash."""
    assert adapter.extract_all({}) == {}


def test_extract_handles_missing_db(tmp_path):
    """Missing DB file → empty dict, no exception."""
    adapter = CallHistoryAdapter(db_path=tmp_path / "nope.storedata")
    assert adapter.extract_all({"p_alice": {"name": "A", "phones": ["+14155550100"], "emails": []}}) == {}
