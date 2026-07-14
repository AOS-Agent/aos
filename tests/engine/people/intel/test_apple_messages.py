"""Tests for AppleMessagesAdapter.

Builds a tiny fixture chat.db with just the schema the adapter queries,
then exercises the extraction contract.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.apple_messages import (
    APPLE_EPOCH_OFFSET,
    AppleMessagesAdapter,
)
from core.engine.people.intel.types import PersonSignals


# ── helpers ───────────────────────────────────────────────────────────

def iso_to_apple_ns(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> int:
    """Convert a UTC datetime to the Apple ns-since-2001 timestamp format."""
    dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    unix_ts = dt.timestamp()
    ns = int((unix_ts - APPLE_EPOCH_OFFSET) * 1e9)
    return ns


def build_fixture_db(path: Path) -> None:
    """Create a minimal chat.db fixture with data exercising all signal paths."""
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE handle (
                ROWID INTEGER PRIMARY KEY,
                id TEXT,
                service TEXT
            );
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY
            );
            CREATE TABLE chat_handle_join (
                chat_id INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                handle_id INTEGER,
                text TEXT,
                date INTEGER,
                is_from_me INTEGER,
                service TEXT,
                associated_message_type INTEGER,
                cache_has_attachments INTEGER
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER,
                message_id INTEGER
            );
            CREATE TABLE attachment (
                ROWID INTEGER PRIMARY KEY,
                mime_type TEXT,
                transfer_name TEXT
            );
            CREATE TABLE message_attachment_join (
                message_id INTEGER,
                attachment_id INTEGER
            );
            """
        )

        # Handles: phone + email for alice (pid=p_alice), unrelated phone, unmatched email.
        handles = [
            (1, "+14155550123", "iMessage"),   # alice phone
            (2, "alice@example.com", "iMessage"),  # alice email
            (3, "+14155550999", "SMS"),  # unrelated
        ]
        cur.executemany(
            "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, ?)", handles
        )

        # Chats: one per handle.
        cur.executemany("INSERT INTO chat (ROWID) VALUES (?)", [(10,), (20,), (30,)])
        cur.executemany(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
            [(10, 1), (20, 2), (30, 3)],
        )

        # Messages. 6 with alice (across 2 handles + 2 months), 1 unrelated.
        # Columns: rowid, handle_id, text, date, is_from_me, service, amt, afc
        messages = [
            # alice phone, Jan 2025
            (
                100,
                1,
                "Hey are you around?",
                iso_to_apple_ns(2025, 1, 10, 14, 0),
                0,
                "iMessage",
                0,
                0,
            ),
            (
                101,
                1,
                "yes just landed",
                iso_to_apple_ns(2025, 1, 10, 14, 5),
                1,
                "iMessage",
                0,
                0,
            ),
            (
                102,
                1,
                "check this out http://example.com",
                iso_to_apple_ns(2025, 1, 10, 14, 30),
                0,
                "iMessage",
                0,
                0,
            ),
            # alice email, Feb 2025
            (
                103,
                2,
                "dinner tonight?",
                iso_to_apple_ns(2025, 2, 12, 19, 0),
                1,
                "iMessage",
                0,
                0,
            ),
            (
                104,
                2,
                "sure 8pm",
                iso_to_apple_ns(2025, 2, 12, 19, 10),
                0,
                "iMessage",
                0,
                0,
            ),
            (
                105,
                2,
                None,  # attachment-only, no text
                iso_to_apple_ns(2025, 2, 12, 19, 15),
                1,
                "iMessage",
                0,
                1,
            ),
            # unrelated message (handle 3)
            (
                200,
                3,
                "spam",
                iso_to_apple_ns(2025, 1, 5, 3, 0),
                0,
                "SMS",
                0,
                0,
            ),
        ]
        cur.executemany(
            """
            INSERT INTO message (
                ROWID, handle_id, text, date, is_from_me, service,
                associated_message_type, cache_has_attachments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            messages,
        )

        # Link each message to the chat matching its handle.
        cmj = [
            (10, 100), (10, 101), (10, 102),
            (20, 103), (20, 104), (20, 105),
            (30, 200),
        ]
        cur.executemany(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", cmj
        )

        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "chat.db"
    build_fixture_db(db)
    return db


@pytest.fixture
def person_index() -> dict[str, dict]:
    return {
        "p_alice": {
            "name": "Alice",
            "phones": ["+1 (415) 555-0123"],
            "emails": ["Alice@Example.com"],
            "wa_jids": [],
        },
        "p_bob": {
            "name": "Bob",
            "phones": ["+14155550000"],
            "emails": [],
            "wa_jids": [],
        },
    }


# ── tests ─────────────────────────────────────────────────────────────

def test_is_available_when_db_exists(fixture_db: Path) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    assert adapter.is_available() is True


def test_is_available_false_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    adapter = AppleMessagesAdapter(db_path=missing)
    assert adapter.is_available() is False


def test_extract_returns_dict(fixture_db: Path, person_index: dict[str, dict]) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert isinstance(result, dict)
    for pid, ps in result.items():
        assert isinstance(pid, str)
        assert isinstance(ps, PersonSignals)


def test_extract_matches_phone_and_email_to_same_person(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert "p_alice" in result
    ps = result["p_alice"]
    assert len(ps.communication) == 1
    # 3 messages from phone handle + 3 from email handle = 6
    assert ps.communication[0].total_messages == 6
    assert ps.source_coverage == ["apple_messages"]


def test_total_sent_received_split_correct(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    # From fixture: sent ids 101, 103, 105 = 3; received ids 100, 102, 104 = 3
    assert comm.sent == 3
    assert comm.received == 3
    assert comm.total_messages == comm.sent + comm.received


def test_temporal_buckets_populated(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    assert "2025-01" in comm.temporal_buckets
    assert "2025-02" in comm.temporal_buckets
    assert comm.temporal_buckets["2025-01"] == 3
    assert comm.temporal_buckets["2025-02"] == 3


def test_links_counted(fixture_db: Path, person_index: dict[str, dict]) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    assert comm.links_shared == 1


def test_sample_messages_capped(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    assert len(comm.sample_messages) <= 5
    for s in comm.sample_messages:
        assert s["channel"] == "imessage"
        assert s["direction"] in ("sent", "received")
        assert s["text"]
        assert s["date"]


def test_unmatched_handle_absent_from_result(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    # Bob has no matching handles in chat.db.
    assert "p_bob" not in result


def test_media_and_attachments(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    # One sent message with cache_has_attachments > 0
    assert comm.media_sent == 1
    assert comm.media_received == 0


def test_service_breakdown_populated(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    assert comm.service_breakdown.get("iMessage") == 6


def test_response_latency_computed(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    # There are direction flips in the fixture; latency should be set.
    assert comm.response_latency_median is not None
    assert comm.response_latency_avg is not None
    assert comm.response_latency_median > 0


def test_first_last_dates_iso(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    comm = result["p_alice"].communication[0]
    assert comm.first_message_date is not None
    assert comm.last_message_date is not None
    assert comm.first_message_date.startswith("2025-01-10")
    assert comm.last_message_date.startswith("2025-02-12")


def test_extract_empty_person_index(fixture_db: Path) -> None:
    adapter = AppleMessagesAdapter(db_path=fixture_db)
    result = adapter.extract_all({})
    assert result == {}


def test_extract_when_unavailable(tmp_path: Path, person_index: dict[str, dict]) -> None:
    adapter = AppleMessagesAdapter(db_path=tmp_path / "missing.db")
    assert adapter.extract_all(person_index) == {}
