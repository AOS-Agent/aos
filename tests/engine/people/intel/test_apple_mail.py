"""Tests for the Apple Mail signal adapter.

Builds a tiny fixture Envelope Index SQLite database in tmp_path
containing the minimal tables required by the adapter, then verifies
extraction output.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.apple_mail import AppleMailAdapter


# ── fixture builder ──────────────────────────────────────────────────


def _ts(year: int, month: int, day: int = 1, hour: int = 12) -> int:
    """Return unix seconds for a UTC date."""
    return int(datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).timestamp())


def _build_envelope_index(
    db_path: Path,
    *,
    with_addresses_table: bool = False,
    with_recipients: bool = True,
) -> None:
    """Build a tiny Envelope Index matching the real schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE messages (
            ROWID INTEGER PRIMARY KEY,
            date_sent INTEGER,
            date_received INTEGER,
            sender INTEGER,
            subject INTEGER,
            mailbox INTEGER,
            conversation_id INTEGER
        );
        CREATE TABLE senders (
            ROWID INTEGER PRIMARY KEY,
            address TEXT,
            comment TEXT
        );
        CREATE TABLE mailboxes (
            ROWID INTEGER PRIMARY KEY,
            url TEXT,
            total_count INTEGER
        );
        CREATE TABLE subjects (
            ROWID INTEGER PRIMARY KEY,
            subject TEXT
        );
        """
    )

    if with_recipients:
        cur.execute(
            """
            CREATE TABLE recipients (
                ROWID INTEGER PRIMARY KEY,
                message_id INTEGER,
                address_id INTEGER,
                type INTEGER
            )
            """
        )

    if with_addresses_table:
        cur.execute(
            """
            CREATE TABLE addresses (
                ROWID INTEGER PRIMARY KEY,
                address TEXT,
                comment TEXT
            )
            """
        )

    # Mailboxes.
    cur.execute(
        "INSERT INTO mailboxes (ROWID, url, total_count) VALUES (?, ?, ?)",
        (1, "imap://you@example.com/INBOX", 0),
    )
    cur.execute(
        "INSERT INTO mailboxes (ROWID, url, total_count) VALUES (?, ?, ?)",
        (2, "local://Sent", 0),
    )

    # Senders.
    cur.execute(
        "INSERT INTO senders (ROWID, address, comment) VALUES (?, ?, ?)",
        (1, "alice@example.com", "Alice"),
    )
    cur.execute(
        "INSERT INTO senders (ROWID, address, comment) VALUES (?, ?, ?)",
        (2, "bob@example.com", "Bob"),
    )
    cur.execute(
        "INSERT INTO senders (ROWID, address, comment) VALUES (?, ?, ?)",
        (3, "noreply@shop.com", "Shop"),
    )
    # Also create a sender row for "you" so recipients that point to senders
    # can reference you — not strictly needed but realistic.
    cur.execute(
        "INSERT INTO senders (ROWID, address, comment) VALUES (?, ?, ?)",
        (4, "you@example.com", "Me"),
    )

    # If using a separate addresses table, populate it for alice/bob/you.
    if with_addresses_table:
        cur.execute(
            "INSERT INTO addresses (ROWID, address, comment) VALUES (?, ?, ?)",
            (1, "alice@example.com", "Alice"),
        )
        cur.execute(
            "INSERT INTO addresses (ROWID, address, comment) VALUES (?, ?, ?)",
            (2, "bob@example.com", "Bob"),
        )
        cur.execute(
            "INSERT INTO addresses (ROWID, address, comment) VALUES (?, ?, ?)",
            (3, "you@example.com", "Me"),
        )

    # Subjects.
    subjects = {
        1: "Project update",
        2: "meeting tomorrow",
        3: "Re: Project update",
        4: "Order receipt #12345",
        5: "Your order shipped",
        6: "Hey how are you",
    }
    for rid, text in subjects.items():
        cur.execute(
            "INSERT INTO subjects (ROWID, subject) VALUES (?, ?)", (rid, text)
        )

    # Messages.
    # Alice: 3 received + 1 sent → all share conversation_id=1.
    # Bob: 1 received, conversation_id=2.
    # noreply: 1 received, conversation_id=3.
    m1_ts = _ts(2026, 2, 10)   # Alice recv — Project update (feb)
    m2_ts = _ts(2026, 2, 11)   # Alice recv — meeting tomorrow (feb)
    m3_ts = _ts(2026, 3, 1)    # Alice sent — Re: Project update (mar)
    m4_ts = _ts(2026, 2, 15)   # Bob recv — Order receipt (feb)
    m5_ts = _ts(2026, 3, 2)    # noreply recv — shipped (mar)
    m6_ts = _ts(2026, 3, 10)   # Alice recv — Hey how are you (mar)

    rows = [
        # (ROWID, date_sent, date_received, sender, subject, mailbox, conv)
        (1, m1_ts, m1_ts, 1, 1, 1, 1),   # Alice → inbox
        (2, m2_ts, m2_ts, 1, 2, 1, 1),   # Alice → inbox
        (3, m3_ts, m3_ts, 4, 3, 2, 1),   # you → sent
        (4, m4_ts, m4_ts, 2, 4, 1, 2),   # Bob → inbox
        (5, m5_ts, m5_ts, 3, 5, 1, 3),   # noreply → inbox
        (6, m6_ts, m6_ts, 1, 6, 1, 1),   # Alice → inbox
    ]
    for r in rows:
        cur.execute(
            "INSERT INTO messages "
            "(ROWID, date_sent, date_received, sender, subject, mailbox, conversation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            r,
        )

    if with_recipients:
        # Sent message (ROWID=3) went to Alice. The address_id resolves
        # against either `addresses` (if present) or `senders`.
        if with_addresses_table:
            alice_aid = 1  # addresses.ROWID
        else:
            alice_aid = 1  # senders.ROWID for alice
        cur.execute(
            "INSERT INTO recipients (message_id, address_id, type) VALUES (?, ?, ?)",
            (3, alice_aid, 0),
        )

    conn.commit()
    conn.close()


def _person_index() -> dict[str, dict]:
    return {
        "p_alice": {
            "name": "Alice",
            "phones": [],
            "emails": ["alice@example.com"],
            "wa_jids": [],
        },
        "p_bob": {
            "name": "Bob",
            "phones": [],
            "emails": ["bob@example.com"],
            "wa_jids": [],
        },
    }


# ── tests ────────────────────────────────────────────────────────────


def test_is_available_true_false(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    assert AppleMailAdapter(db_path=db).is_available() is True

    missing = tmp_path / "does-not-exist"
    assert AppleMailAdapter(db_path=missing).is_available() is False


def test_extract_returns_dict(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    assert isinstance(result, dict)
    assert "p_alice" in result
    assert "p_bob" in result


def test_sent_received_split_correct(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.sent_to_you == 3
    assert alice_sig.you_sent == 1
    assert alice_sig.total_emails == 4


def test_bidirectional_ratio(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.bidirectional_ratio == pytest.approx(1 / 3)


def test_automated_sender_excluded(tmp_path: Path) -> None:
    """noreply@shop.com must not show up in any person signal."""
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    idx = _person_index()
    idx["p_shop"] = {
        "name": "Shop",
        "phones": [],
        "emails": ["noreply@shop.com"],
        "wa_jids": [],
    }
    result = AppleMailAdapter(db_path=db).extract_all(idx)
    # The automated-sender regex drops noreply@shop.com entirely.
    assert "p_shop" not in result
    # And no person's total should include it.
    for ps in result.values():
        for sig in ps.professional:
            assert "automated" not in sig.subject_categories


def test_subject_categories_counted(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    cats = alice_sig.subject_categories
    # "Project update", "meeting tomorrow", "Re: Project update" → professional
    # "Hey how are you" → personal
    assert cats.get("professional", 0) >= 1
    assert cats.get("personal", 0) >= 1

    bob_sig = result["p_bob"].professional[0]
    assert bob_sig.subject_categories.get("transactional", 0) >= 1


def test_temporal_buckets_populated(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    buckets = alice_sig.temporal_buckets
    # Alice emails span Feb + Mar 2026.
    assert "2026-02" in buckets
    assert "2026-03" in buckets
    assert sum(buckets.values()) == 4


def test_thread_count_and_depth(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.thread_count == 1
    # Conversation 1 has 4 total messages (3 alice recv + 1 sent).
    assert alice_sig.max_thread_depth >= 3
    assert alice_sig.max_thread_depth == 4
    assert alice_sig.avg_thread_depth == pytest.approx(4.0)


def test_first_last_dates_correct(tmp_path: Path) -> None:
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.first_date is not None
    assert alice_sig.last_date is not None
    # Feb 10 is earliest, Mar 10 is latest.
    assert alice_sig.first_date.startswith("2026-02-10")
    assert alice_sig.last_date.startswith("2026-03-10")


def test_picks_highest_v_dir_when_no_path(tmp_path: Path) -> None:
    """When db_path is None, adapter picks V10 over V9."""
    mail_root = tmp_path / "Mail"
    v9 = mail_root / "V9" / "MailData" / "Envelope Index"
    v10 = mail_root / "V10" / "MailData" / "Envelope Index"
    _build_envelope_index(v9)
    _build_envelope_index(v10)

    adapter = AppleMailAdapter(mail_root=mail_root)
    assert adapter.db_path is not None
    assert "V10" in str(adapter.db_path)
    assert adapter.is_available() is True


def test_extract_returns_empty_when_db_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    adapter = AppleMailAdapter(db_path=missing)
    assert adapter.extract_all(_person_index()) == {}


def test_graceful_if_addresses_table_missing(tmp_path: Path) -> None:
    """Default Envelope Index (no addresses table) must still work."""
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db, with_addresses_table=False)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    # Recipients point into senders table, so alice's sent email is attributed.
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.you_sent == 1


def test_graceful_when_addresses_table_present(tmp_path: Path) -> None:
    """Envelope Index with addresses table (newer macOS) must also work."""
    db = tmp_path / "Envelope Index"
    _build_envelope_index(db, with_addresses_table=True)
    result = AppleMailAdapter(db_path=db).extract_all(_person_index())
    alice_sig = result["p_alice"].professional[0]
    assert alice_sig.you_sent == 1
    assert alice_sig.sent_to_you == 3
