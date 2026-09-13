"""contact-sync found 0 macOS contacts in ~90% of its runs over 4 months
(cron audit 2026-09-13, aos#240.4) — a TCC/path failure looked identical to
"genuinely no contacts", both silently returning {"new": 0, "updated": 0,
"unchanged": 0} with exit 0.

These tests pin the fix: version-agnostic AddressBook discovery (multiple
`Sources/<uuid>/` accounts, richest-by-row-count wins — mirroring the same
strategy core/engine/people/intel/sources/apple_contacts.py already uses),
and an explicit, distinguishable outcome — "ok" / "denied" / "not_found" —
recorded to a small status file so a reconcile check can NOTIFY on a real
access failure instead of it reading as quiet success forever.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.engine.comms import sync_contacts as sc


def _build_addressbook_db(path: Path, records: list[tuple[str, str, str]]) -> None:
    """records: list of (first, last, phone)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, "
        "ZLASTNAME TEXT, ZORGANIZATION TEXT, ZNICKNAME TEXT)"
    )
    conn.execute(
        "CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT)"
    )
    conn.execute(
        "CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT)"
    )
    for i, (first, last, phone) in enumerate(records, start=1):
        conn.execute(
            "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME) VALUES (?, ?, ?)",
            (i, first, last),
        )
        if phone:
            conn.execute(
                "INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER) VALUES (?, ?)",
                (i, phone),
            )
    conn.commit()
    conn.close()


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "contact-sync-status.json"
    monkeypatch.setattr(sc, "STATUS_FILE", path)
    return path


def _status(status_file: Path) -> dict:
    return json.loads(status_file.read_text())


# ── path detection: version-agnostic, richest source wins ───────────────


def test_finds_a_newer_schema_version_than_the_hardcoded_v22(tmp_path, status_file):
    """The old code hardcoded `AddressBook-v22.abcddb` — a future macOS schema
    bump must not silently find nothing."""
    ab_dir = tmp_path / "AddressBook"
    src = ab_dir / "Sources" / "SOME-UUID"
    src.mkdir(parents=True)
    _build_addressbook_db(src / "AddressBook-v23.abcddb", [("Alice", "Smith", "+14155550112")])

    contacts = sc.read_mac_contacts_phones(ab_dir=ab_dir)

    assert len(contacts) == 1
    assert next(iter(contacts.values()))["name"] == "Alice Smith"
    assert _status(status_file)["result"] == "ok"


def test_picks_richest_of_multiple_source_accounts(tmp_path, status_file):
    """Real machines have one Sources/<uuid>/ folder per synced account
    (iCloud, Exchange, "On My Mac", ...). The richest by row count is
    almost always the one with real contact data — same strategy
    apple_contacts.py's _resolve_db_path() already uses."""
    ab_dir = tmp_path / "AddressBook"
    empty_src = ab_dir / "Sources" / "EMPTY-ACCOUNT"
    empty_src.mkdir(parents=True)
    _build_addressbook_db(empty_src / "AddressBook-v22.abcddb", [])

    rich_src = ab_dir / "Sources" / "RICH-ACCOUNT"
    rich_src.mkdir(parents=True)
    _build_addressbook_db(
        rich_src / "AddressBook-v22.abcddb",
        [("Bob", "Jones", "+14155550144"), ("Carol", "Lee", "+14155550177")],
    )

    contacts = sc.read_mac_contacts_phones(ab_dir=ab_dir)

    names = {c["name"] for c in contacts.values()}
    assert names == {"Bob Jones", "Carol Lee"}


def test_no_addressbook_sources_at_all_is_not_found(tmp_path, status_file):
    ab_dir = tmp_path / "AddressBook"
    ab_dir.mkdir()  # exists, but no Sources/ and no direct db file

    contacts = sc.read_mac_contacts_phones(ab_dir=ab_dir)

    assert contacts == {}
    assert _status(status_file)["result"] == "not_found"


# ── TCC-style denial: distinguishable from "genuinely no contacts" ──────


def test_denied_access_logs_one_clear_line_and_exits_cleanly(tmp_path, status_file, capsys):
    ab_dir = tmp_path / "AddressBook"
    src = ab_dir / "Sources" / "SOME-UUID"
    src.mkdir(parents=True)
    db = src / "AddressBook-v22.abcddb"
    _build_addressbook_db(db, [("Alice", "Smith", "+14155550112")])
    db.chmod(0o000)

    try:
        contacts = sc.read_mac_contacts_phones(ab_dir=ab_dir)
    finally:
        db.chmod(0o644)  # restore so tmp_path cleanup can remove it

    assert contacts == {}
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "denied" in ln.lower() or "permission" in ln.lower()]
    assert len(lines) == 1, f"expected exactly one clear diagnostic line, got: {lines}"
    assert _status(status_file)["result"] == "denied"


def test_sync_never_pretends_success_on_denial(tmp_path, status_file, monkeypatch):
    """sync() must not report a misleadingly-clean 0/0/0 the same way a
    genuinely-empty-but-accessible AddressBook would — the caller (main())
    still exits 0 either way (this is an expected, non-crash condition, not a
    bug in the running process), but the status file is what distinguishes
    them for reconcile."""
    ab_dir = tmp_path / "AddressBook"
    src = ab_dir / "Sources" / "SOME-UUID"
    src.mkdir(parents=True)
    db = src / "AddressBook-v22.abcddb"
    _build_addressbook_db(db, [("Alice", "Smith", "+14155550112")])
    db.chmod(0o000)
    monkeypatch.setattr(sc, "_addressbook_dir", lambda: ab_dir)

    try:
        stats = sc.sync(dry_run=True)
    finally:
        db.chmod(0o644)

    assert stats == {"new": 0, "updated": 0, "unchanged": 0}
    assert _status(status_file)["result"] == "denied"
