"""Tests for the Apple Contacts signal adapter.

Builds a tiny AddressBook-v22.abcddb fixture with a handful of ZABCDRECORD
rows plus the supporting side-tables, then exercises the adapter end-to-end.
Schema mirrors the minimal subset the production adapter reads — this is
schema-guess territory, so tests are the source of truth for field names.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.apple_contacts import (
    APPLE_EPOCH_OFFSET,
    AppleContactsAdapter,
)


# ── Fixture builder ──────────────────────────────────────────────────

def _core_data_ts(year: int, month: int, day: int) -> float:
    """Return a Core-Data timestamp (seconds since 2001-01-01) for a date."""
    dt = datetime(year, month, day, tzinfo=timezone.utc)
    return dt.timestamp() - APPLE_EPOCH_OFFSET


def _build_db(path: Path, *, include_social: bool = True,
              include_urls: bool = True, include_groups: bool = True) -> None:
    """Create a minimal AddressBook-v22.abcddb fixture at the given path."""
    conn = sqlite3.connect(str(path))
    c = conn.cursor()

    # Mirror the real modern macOS schema: ZNOTE is an INTEGER flag in
    # ZABCDRECORD; the actual note text lives in ZABCDNOTE(ZCONTACT, ZTEXT).
    c.execute(
        """CREATE TABLE ZABCDRECORD (
            Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME TEXT,
            ZLASTNAME TEXT,
            ZORGANIZATION TEXT,
            ZJOBTITLE TEXT,
            ZNOTE INTEGER,
            ZBIRTHDAY REAL,
            ZCREATIONDATE REAL
        )"""
    )
    c.execute(
        """CREATE TABLE ZABCDNOTE (
            Z_PK INTEGER PRIMARY KEY,
            ZCONTACT INTEGER,
            ZTEXT TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE ZABCDPHONENUMBER (
            Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER,
            ZFULLNUMBER TEXT,
            ZLABEL TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE ZABCDEMAILADDRESS (
            Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER,
            ZADDRESS TEXT,
            ZLABEL TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE ZABCDPOSTALADDRESS (
            Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER,
            ZCITY TEXT,
            ZCOUNTRYNAME TEXT,
            ZSTREET TEXT,
            ZLABEL TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE ZABCDRELATEDNAME (
            Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER,
            ZLABEL TEXT,
            ZNAME TEXT
        )"""
    )

    if include_urls:
        c.execute(
            """CREATE TABLE ZABCDURLADDRESS (
                Z_PK INTEGER PRIMARY KEY,
                ZOWNER INTEGER,
                ZURL TEXT,
                ZLABEL TEXT
            )"""
        )

    if include_social:
        c.execute(
            """CREATE TABLE ZABCDSOCIALPROFILE (
                Z_PK INTEGER PRIMARY KEY,
                ZOWNER INTEGER,
                ZSERVICENAME TEXT,
                ZUSERNAME TEXT
            )"""
        )

    if include_groups:
        c.execute(
            """CREATE TABLE ZABCDGROUP (
                Z_PK INTEGER PRIMARY KEY,
                ZNAME TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE Z_22PARENTGROUPS (
                Z_22PARENTGROUPS1 INTEGER,
                Z_22GROUPS INTEGER
            )"""
        )

    # Records
    alice_birthday = _core_data_ts(1990, 5, 15)
    alice_created = _core_data_ts(2020, 1, 10)
    c.executemany(
        "INSERT INTO ZABCDRECORD "
        "(Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZJOBTITLE, ZNOTE, "
        "ZBIRTHDAY, ZCREATIONDATE) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # ZNOTE is now an INTEGER flag (1 = has note, 0 = no note).
            # Real text goes into ZABCDNOTE below.
            (1, "Alice", "Smith", "Acme Corp", "Engineer", 1, alice_birthday, alice_created),
            (2, "Bob", "Jones", None, None, 0, None, None),
            (3, "Carol", "Anderson", None, None, 0, None, None),
            (4, "Carol", "Bennett", None, None, 0, None, None),
        ],
    )
    c.execute(
        "INSERT INTO ZABCDNOTE (ZCONTACT, ZTEXT) VALUES (?, ?)",
        (1, "Met at conference 2019. Loves hiking."),
    )

    # Phones
    c.executemany(
        "INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER, ZLABEL) "
        "VALUES (?, ?, ?)",
        [
            (1, "+1-555-111-2222", "mobile"),
            (1, "555 111 3333", "home"),
            (2, "+1 (555) 444-5555", "mobile"),
            (3, "+1-555-777-8888", "mobile"),
            (4, "+1-555-999-0000", "mobile"),
        ],
    )

    # Emails
    c.execute(
        "INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS, ZLABEL) "
        "VALUES (?, ?, ?)",
        (1, "alice@example.com", "home"),
    )

    # Postal
    c.execute(
        "INSERT INTO ZABCDPOSTALADDRESS "
        "(ZOWNER, ZCITY, ZCOUNTRYNAME, ZSTREET, ZLABEL) VALUES (?, ?, ?, ?, ?)",
        (1, "Brooklyn", "USA", "123 Main St", "home"),
    )

    # Related names
    c.execute(
        "INSERT INTO ZABCDRELATEDNAME (ZOWNER, ZLABEL, ZNAME) VALUES (?, ?, ?)",
        (1, "sister", "Riley"),
    )

    if include_urls:
        c.execute(
            "INSERT INTO ZABCDURLADDRESS (ZOWNER, ZURL, ZLABEL) VALUES (?, ?, ?)",
            (1, "https://alice.example.com", "homepage"),
        )

    if include_social:
        c.execute(
            "INSERT INTO ZABCDSOCIALPROFILE (ZOWNER, ZSERVICENAME, ZUSERNAME) "
            "VALUES (?, ?, ?)",
            (1, "twitter", "@alice"),
        )

    if include_groups:
        c.execute(
            "INSERT INTO ZABCDGROUP (Z_PK, ZNAME) VALUES (?, ?)",
            (100, "Family"),
        )
        c.execute(
            "INSERT INTO Z_22PARENTGROUPS (Z_22PARENTGROUPS1, Z_22GROUPS) "
            "VALUES (?, ?)",
            (1, 100),
        )

    conn.commit()
    conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    """Full-featured fixture with every optional table populated."""
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db(db)
    return db


@pytest.fixture
def person_index_all() -> dict[str, dict]:
    return {
        "p_alice": {
            "name": "Alice Smith",
            "phones": ["+15551112222"],
            "emails": ["alice@example.com"],
            "wa_jids": [],
        },
        "p_bob": {
            "name": "Bob Jones",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        },
        "p_carol": {
            "name": "Carol",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        },
    }


# ── Tests ─────────────────────────────────────────────────────────

def test_is_available_true_false(tmp_path, fixture_db):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    assert adapter.is_available() is True

    missing = tmp_path / "nope.abcddb"
    adapter_missing = AppleContactsAdapter(db_path=str(missing))
    assert adapter_missing.is_available() is False


def test_picks_richest_when_no_path_given(tmp_path, monkeypatch):
    # Build two candidate DBs with different record counts.
    src_a = tmp_path / "SourceA"
    src_a.mkdir()
    db_a = src_a / "AddressBook-v22.abcddb"
    _build_db(db_a)  # 4 records

    src_b = tmp_path / "SourceB"
    src_b.mkdir()
    db_b = src_b / "AddressBook-v22.abcddb"
    conn = sqlite3.connect(str(db_b))
    conn.execute(
        """CREATE TABLE ZABCDRECORD (
            Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT,
            ZJOBTITLE TEXT, ZNOTE TEXT, ZBIRTHDAY REAL, ZCREATIONDATE REAL
        )"""
    )
    for i in range(10):
        conn.execute(
            "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME) VALUES (?, ?)",
            (i + 1, f"Person{i}"),
        )
    conn.commit()
    conn.close()

    # Monkeypatch the class-level glob pattern to find both.
    pattern = str(tmp_path / "Source*" / "AddressBook-v22.abcddb")
    monkeypatch.setattr(AppleContactsAdapter, "_glob_pattern", pattern)

    adapter = AppleContactsAdapter()
    resolved = adapter._resolve_db_path()
    assert resolved == str(db_b), f"Expected SourceB (10 records), got {resolved}"


def test_matches_by_full_name(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    assert "p_alice" in results
    meta = results["p_alice"].metadata[0]
    assert meta.source == "apple_contacts"


def test_matches_by_phone_suffix_when_name_differs(fixture_db):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    idx = {
        "p_x": {
            "name": "Totally Different Name",
            "phones": ["+1-555-111-2222"],
            "emails": [],
            "wa_jids": [],
        }
    }
    results = adapter.extract_all(idx)
    assert "p_x" in results
    # Matched the Alice record via phone
    meta = results["p_x"].metadata[0]
    assert meta.has_birthday is True


def test_rejects_ambiguous_first_name_match(fixture_db):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    idx = {
        "p_carol": {
            "name": "Carol",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        }
    }
    results = adapter.extract_all(idx)
    # Two Carols exist — should NOT match
    assert "p_carol" not in results


def test_metadata_signal_populated(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    meta = results["p_alice"].metadata[0]
    assert meta.has_birthday is True
    assert meta.birthday == "1990-05-15"
    assert meta.has_address is True
    assert meta.addresses[0]["city"] == "Brooklyn"
    assert meta.has_notes is True
    assert meta.notes_snippet.startswith("Met at conference")
    assert meta.has_social_profiles is True
    assert meta.social_profiles[0]["platform"] == "twitter"
    assert meta.has_related_names is True
    assert meta.has_urls is True
    assert meta.organization_raw == "Acme Corp"
    assert meta.job_title == "Engineer"
    assert meta.contact_created_at is not None
    # Birthday, address, notes, social, related, urls, org, job, groups = 9
    assert meta.richness_score > 5


def test_related_names_captured(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    meta = results["p_alice"].metadata[0]
    assert len(meta.related_names) == 1
    rn = meta.related_names[0]
    assert rn["label"] == "sister"
    assert rn["name"] == "Riley"


def test_group_signal_emitted_for_family_group(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    signals = results["p_alice"]
    assert len(signals.group_membership) == 1
    gs = signals.group_membership[0]
    assert gs.source == "apple_contacts"
    assert gs.total_groups == 1
    assert gs.shared_with_operator == 1
    assert gs.groups[0]["name"] == "Family"
    assert gs.groups[0]["type"] == "apple_contacts"
    assert gs.group_categories.get("family", 0) >= 1


def test_no_group_signal_when_no_groups(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    # Bob is in the index but belongs to no groups
    assert "p_bob" in results
    assert results["p_bob"].group_membership == []


def test_handles_missing_social_profile_table(tmp_path, person_index_all):
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db(db, include_social=False)
    adapter = AppleContactsAdapter(db_path=str(db))
    results = adapter.extract_all(person_index_all)
    assert "p_alice" in results
    meta = results["p_alice"].metadata[0]
    assert meta.has_social_profiles is False
    assert meta.social_profiles == []
    # Other fields should still populate
    assert meta.has_birthday is True
    assert meta.has_related_names is True


def test_extract_returns_dict(fixture_db, person_index_all):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    results = adapter.extract_all(person_index_all)
    assert isinstance(results, dict)
    for pid, signals in results.items():
        assert isinstance(pid, str)
        assert signals.person_id == pid


# ── ZBIRTHDAYYEARLESS second-pass tests ─────────────────────────────


def _build_db_with_yearless(
    path: Path,
    *,
    include_yearless: bool = True,
    include_year_aware: bool = True,
) -> None:
    """Build a fixture where ZBIRTHDAYYEARLESS column is present.

    Controls whether each row gets a year-aware ZBIRTHDAY, a yearless
    birthday, both, or a year-aware-but-out-of-range timestamp.
    """
    conn = sqlite3.connect(str(path))
    c = conn.cursor()

    cols = [
        "Z_PK INTEGER PRIMARY KEY",
        "ZFIRSTNAME TEXT",
        "ZLASTNAME TEXT",
        "ZORGANIZATION TEXT",
        "ZJOBTITLE TEXT",
        "ZNOTE INTEGER",
        "ZBIRTHDAY REAL",
        "ZCREATIONDATE REAL",
    ]
    if include_yearless:
        cols.append("ZBIRTHDAYYEARLESS REAL")
    c.execute(f"CREATE TABLE ZABCDRECORD ({', '.join(cols)})")
    c.execute(
        """CREATE TABLE ZABCDNOTE (
            Z_PK INTEGER PRIMARY KEY,
            ZCONTACT INTEGER,
            ZTEXT TEXT
        )"""
    )

    # Records:
    #  1 = Dana Only-Yearless (only ZBIRTHDAYYEARLESS populated)
    #  2 = Eli Both (both columns populated → year-aware wins)
    #  3 = Finn OutOfRange + Yearless (ZBIRTHDAY year < 1900, yearless falls back)
    rows = []
    # Core Data ts for yearless values (year doesn't matter — we emit --MM-DD).
    march_07 = _core_data_ts(2000, 3, 7)  # → "--03-07"
    june_21 = _core_data_ts(2000, 6, 21)  # → "--06-21"
    oct_11 = _core_data_ts(2000, 10, 11)  # → "--10-11"

    eli_birthday = _core_data_ts(1985, 7, 4)  # → "1985-07-04"

    # Out-of-range: year 1800, rejected by year-range check.
    finn_old_ts = datetime(1800, 1, 1, tzinfo=timezone.utc).timestamp() - APPLE_EPOCH_OFFSET

    def _mkrow(pk, first, last, birthday, yearless):
        if include_yearless:
            return (pk, first, last, None, None, 0, birthday, None, yearless)
        return (pk, first, last, None, None, 0, birthday, None)

    if include_year_aware:
        rows.append(_mkrow(1, "Dana", "Yearless", None, march_07))
        rows.append(_mkrow(2, "Eli", "Both", eli_birthday, june_21))
        rows.append(_mkrow(3, "Finn", "Old", finn_old_ts, oct_11))
    else:
        rows.append(_mkrow(1, "Dana", "Yearless", None, march_07))

    placeholders = ",".join("?" * len(rows[0]))
    colnames = ",".join(
        [
            "Z_PK",
            "ZFIRSTNAME",
            "ZLASTNAME",
            "ZORGANIZATION",
            "ZJOBTITLE",
            "ZNOTE",
            "ZBIRTHDAY",
            "ZCREATIONDATE",
        ]
        + (["ZBIRTHDAYYEARLESS"] if include_yearless else [])
    )
    c.executemany(
        f"INSERT INTO ZABCDRECORD ({colnames}) VALUES ({placeholders})",
        rows,
    )

    conn.commit()
    conn.close()


def test_yearless_only_renders_mm_dd(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db_with_yearless(db)
    adapter = AppleContactsAdapter(db_path=str(db))
    idx = {"p_dana": {"name": "Dana Yearless", "phones": [], "emails": [], "wa_jids": []}}
    results = adapter.extract_all(idx)
    assert "p_dana" in results
    meta = results["p_dana"].metadata[0]
    assert meta.has_birthday is True
    assert meta.birthday == "--03-07"


def test_year_aware_wins_when_both_present(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db_with_yearless(db)
    adapter = AppleContactsAdapter(db_path=str(db))
    idx = {"p_eli": {"name": "Eli Both", "phones": [], "emails": [], "wa_jids": []}}
    results = adapter.extract_all(idx)
    assert "p_eli" in results
    meta = results["p_eli"].metadata[0]
    assert meta.has_birthday is True
    # Year-aware ZBIRTHDAY takes precedence.
    assert meta.birthday == "1985-07-04"


def test_yearless_fallback_when_year_out_of_range(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db_with_yearless(db)
    adapter = AppleContactsAdapter(db_path=str(db))
    idx = {"p_finn": {"name": "Finn Old", "phones": [], "emails": [], "wa_jids": []}}
    results = adapter.extract_all(idx)
    assert "p_finn" in results
    meta = results["p_finn"].metadata[0]
    # ZBIRTHDAY year is 1800 — rejected by range check. Yearless wins.
    assert meta.has_birthday is True
    assert meta.birthday == "--10-11"


def test_yearless_column_missing_is_graceful(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    _build_db_with_yearless(db, include_yearless=False)
    adapter = AppleContactsAdapter(db_path=str(db))
    idx = {"p_dana": {"name": "Dana Yearless", "phones": [], "emails": [], "wa_jids": []}}
    # Adapter must not crash, just no birthday for Dana.
    results = adapter.extract_all(idx)
    assert "p_dana" in results
    meta = results["p_dana"].metadata[0]
    assert meta.has_birthday is False
    assert meta.birthday is None


def test_unmatched_person_absent(fixture_db):
    adapter = AppleContactsAdapter(db_path=str(fixture_db))
    idx = {
        "p_ghost": {
            "name": "Nobody Here",
            "phones": ["+10000000000"],
            "emails": [],
            "wa_jids": [],
        }
    }
    results = adapter.extract_all(idx)
    assert "p_ghost" not in results
