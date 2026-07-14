"""Tests for the WhatsApp signal adapter.

Builds a tiny fixture ChatStorage.sqlite in tmp_path with the minimum
Z-prefixed columns the adapter actually reads. No real WhatsApp data.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.whatsapp import (
    APPLE_EPOCH_OFFSET,
    WhatsAppAdapter,
)


# ── Fixture helpers ────────────────────────────────────────────────────

ALICE_JID = "14155550123@s.whatsapp.net"
GROUP_JID = "120363000000000000@g.us"
OPERATOR_JID = "14155559999@s.whatsapp.net"
# A person identified only by phone number (no JID in index)
BOB_JID = "442071234567@s.whatsapp.net"


def _unix_to_apple(ts: float) -> float:
    return ts - APPLE_EPOCH_OFFSET


def _mk_apple_ts(year: int, month: int, day: int = 15, hour: int = 12) -> float:
    dt = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    return _unix_to_apple(dt.timestamp())


def _build_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE ZWACHATSESSION (
                Z_PK           INTEGER PRIMARY KEY,
                ZSESSIONTYPE   INTEGER,
                ZCONTACTJID    TEXT,
                ZPARTNERNAME   TEXT,
                ZMESSAGECOUNTER INTEGER
            );

            CREATE TABLE ZWAMESSAGE (
                Z_PK         INTEGER PRIMARY KEY,
                ZCHATSESSION INTEGER,
                ZFROMJID     TEXT,
                ZTOJID       TEXT,
                ZMESSAGEDATE REAL,
                ZMESSAGETYPE INTEGER,
                ZTEXT        TEXT,
                ZISFROMME    INTEGER
            );

            CREATE TABLE ZWAGROUPMEMBER (
                Z_PK         INTEGER PRIMARY KEY,
                ZCHATSESSION INTEGER,
                ZMEMBERJID   TEXT,
                ZCONTACTNAME TEXT
            );
            """
        )

        # Sessions: 1 individual with Alice, 1 group "Family Group",
        # 1 individual with Bob (phone-only match).
        conn.execute(
            "INSERT INTO ZWACHATSESSION (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME) "
            "VALUES (?,?,?,?)",
            (1, 0, ALICE_JID, "Alice"),
        )
        conn.execute(
            "INSERT INTO ZWACHATSESSION (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME) "
            "VALUES (?,?,?,?)",
            (2, 1, GROUP_JID, "Family Group"),
        )
        conn.execute(
            "INSERT INTO ZWACHATSESSION (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME) "
            "VALUES (?,?,?,?)",
            (3, 0, BOB_JID, "Bob"),
        )

        # Two different months for temporal buckets.
        ts_jan = _mk_apple_ts(2025, 1, 10, 10)  # 10:00 UTC, business hours
        ts_feb = _mk_apple_ts(2025, 2, 20, 23)  # 23:00 UTC, late night

        # Alice messages (5 total):
        #  - text sent (ts_jan, business hours)
        #  - text received (ts_jan + 60s, latency flip)
        #  - voice note received (ts_feb)
        #  - image sent (ts_feb)
        #  - link-type message received (ts_feb)
        alice_msgs = [
            (1, ALICE_JID, OPERATOR_JID, ts_jan, 0, "Hello there", 1),
            (1, OPERATOR_JID, ALICE_JID, ts_jan + 60, 0, "Hi Alice", 0),
            (1, ALICE_JID, OPERATOR_JID, ts_feb, 2, None, 0),             # voice note recv
            (1, OPERATOR_JID, ALICE_JID, ts_feb + 10, 1, None, 1),        # image sent
            (1, ALICE_JID, OPERATOR_JID, ts_feb + 20, 7, "check https://example.com", 0),  # link
        ]
        for i, (sess, frm, to, mdate, mtype, txt, fromme) in enumerate(alice_msgs, start=1):
            conn.execute(
                "INSERT INTO ZWAMESSAGE "
                "(ZCHATSESSION, ZFROMJID, ZTOJID, ZMESSAGEDATE, ZMESSAGETYPE, ZTEXT, ZISFROMME) "
                "VALUES (?,?,?,?,?,?,?)",
                (sess, frm, to, mdate, mtype, txt, fromme),
            )

        # Bob: 1 text sent, 1 text received — only to prove phone-suffix matching.
        bob_msgs = [
            (3, BOB_JID, OPERATOR_JID, ts_jan, 0, "yo", 0),
            (3, OPERATOR_JID, BOB_JID, ts_jan + 30, 0, "hey", 1),
        ]
        for frm, to, mdate, mtype, txt, fromme in [
            (m[1], m[2], m[3], m[4], m[5], m[6]) for m in bob_msgs
        ]:
            conn.execute(
                "INSERT INTO ZWAMESSAGE "
                "(ZCHATSESSION, ZFROMJID, ZTOJID, ZMESSAGEDATE, ZMESSAGETYPE, ZTEXT, ZISFROMME) "
                "VALUES (?,?,?,?,?,?,?)",
                (3, frm, to, mdate, mtype, txt, fromme),
            )

        # Group members for "Family Group" (session 2): Alice + operator
        conn.execute(
            "INSERT INTO ZWAGROUPMEMBER (ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME) "
            "VALUES (?,?,?)",
            (2, ALICE_JID, "Alice"),
        )
        conn.execute(
            "INSERT INTO ZWAGROUPMEMBER (ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME) "
            "VALUES (?,?,?)",
            (2, OPERATOR_JID, "Operator"),
        )

        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "ChatStorage.sqlite"
    _build_fixture_db(db)
    return db


@pytest.fixture
def person_index() -> dict[str, dict]:
    return {
        "p_alice": {
            "name": "Alice",
            "phones": [],
            "emails": [],
            "wa_jids": [ALICE_JID],
        },
        "p_bob": {
            # Only phone — must match via suffix matching on 4420-...
            "name": "Bob",
            "phones": ["+44 20 7123 4567"],
            "emails": [],
            "wa_jids": [],
        },
        "p_unmatched": {
            "name": "Carol",
            "phones": ["+15559998888"],
            "emails": [],
            "wa_jids": [],
        },
    }


# ── Tests ──────────────────────────────────────────────────────────────

def test_is_available_true_false(tmp_path: Path, fixture_db: Path) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    assert adapter.is_available() is True

    missing = WhatsAppAdapter(db_path=tmp_path / "does_not_exist.sqlite")
    assert missing.is_available() is False


def test_extract_returns_dict(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    assert isinstance(out, dict)
    assert "p_alice" in out


def test_individual_messages_counted(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    sig = out["p_alice"].communication[0]
    assert sig.total_messages == 5
    assert sig.sent == 2    # 1 text sent + 1 image sent
    assert sig.received == 3  # text recv + voice recv + link recv
    assert sig.source == "whatsapp"
    assert sig.channel == "whatsapp"
    assert sig.first_message_date is not None
    assert sig.last_message_date is not None


def test_voice_notes_counted(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    sig = out["p_alice"].communication[0]
    assert sig.voice_notes_received == 1
    assert sig.voice_notes_sent == 0


def test_media_counted(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    sig = out["p_alice"].communication[0]
    # 1 image sent from operator
    assert sig.media_sent == 1
    assert sig.media_received == 0


def test_links_counted(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    sig = out["p_alice"].communication[0]
    assert sig.links_shared >= 1


def test_temporal_buckets_populated(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    sig = out["p_alice"].communication[0]
    assert "2025-01" in sig.temporal_buckets
    assert "2025-02" in sig.temporal_buckets
    assert sig.temporal_pattern != "none"


def test_group_membership(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    assert out["p_alice"].group_membership, "expected at least one GroupSignal"
    gsig = out["p_alice"].group_membership[0]
    assert gsig.total_groups == 1
    assert gsig.shared_with_operator == 1
    assert gsig.groups[0]["name"] == "Family Group"
    assert gsig.groups[0]["type"] == "whatsapp"
    assert gsig.groups[0]["member_count"] == 2
    assert "family" in gsig.group_categories


def test_jid_matching_via_phone_suffix(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    # Bob has no wa_jids in the index, only a phone number.
    # Suffix of 442071234567 → last 10 = 2071234567 which matches BOB_JID digits.
    assert "p_bob" in out
    bob_sig = out["p_bob"].communication[0]
    assert bob_sig.total_messages == 2
    assert bob_sig.sent == 1
    assert bob_sig.received == 1


def test_unmatched_person_absent(fixture_db: Path, person_index: dict) -> None:
    adapter = WhatsAppAdapter(db_path=fixture_db)
    out = adapter.extract_all(person_index)
    assert "p_unmatched" not in out
