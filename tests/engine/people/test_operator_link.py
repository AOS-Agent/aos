"""Tests for the universal operator-link tool.

Imports the script via importlib (it's a CLI script, not a package) and
exercises its pure functions on in-memory data. No real operator data
appears in this file.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "core" / "bin" / "internal" / "operator-link"


@pytest.fixture(scope="module")
def operator_link_module():
    # Script has no .py suffix; explicit SourceFileLoader handles that
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("operator_link", str(SCRIPT))
    spec = importlib.util.spec_from_loader("operator_link", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _make_people_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE people (
            id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL,
            display_name TEXT, first_name TEXT, last_name TEXT, nickname TEXT,
            importance INTEGER DEFAULT 3, is_self INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            created_at INTEGER, updated_at INTEGER, merge_target_id TEXT
        );
        CREATE TABLE person_identifiers (
            person_id TEXT, type TEXT, value TEXT, normalized TEXT, source TEXT,
            PRIMARY KEY (person_id, type, value)
        );
    """)
    return conn


# ── _normalize_phone ────────────────────────────────────────────────


def test_normalize_phone_e164(operator_link_module):
    assert operator_link_module._normalize_phone("+14168453524") == "+14168453524"


def test_normalize_phone_us_no_prefix(operator_link_module):
    # 4168453524 alone won't validate as a US number; +1 prefix should work
    assert operator_link_module._normalize_phone("+1 (416) 845-3524") == "+14168453524"


def test_normalize_phone_rejects_garbage(operator_link_module):
    # GUID-like strings, hex, anything that's not a real phone
    assert operator_link_module._normalize_phone("D34836AB-4EA8-454E-AD16-AD8713680BBE") is None
    assert operator_link_module._normalize_phone("not-a-phone") is None
    assert operator_link_module._normalize_phone("") is None
    assert operator_link_module._normalize_phone("@") is None


def test_normalize_phone_rejects_email(operator_link_module):
    assert operator_link_module._normalize_phone("foo@bar.com") is None


def test_normalize_email(operator_link_module):
    assert operator_link_module._normalize_email("Foo@BAR.com") == "foo@bar.com"
    assert operator_link_module._normalize_email("user@s.whatsapp.net") is None
    assert operator_link_module._normalize_email("group@g.us") is None
    assert operator_link_module._normalize_email("not-email") is None
    assert operator_link_module._normalize_email("") is None


# ── find_operator_by_identifier ─────────────────────────────────────


def test_find_by_phone_match(operator_link_module):
    conn = _make_people_db()
    conn.execute("INSERT INTO people (id, canonical_name, created_at, updated_at) VALUES ('p_x', 'Alice', 0, 0)")
    conn.execute(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) "
        "VALUES ('p_x', 'phone', '+15551234567', '+15551234567')"
    )
    conn.commit()
    found = operator_link_module.find_operator_by_identifier(
        conn, phones={"+15551234567"}, emails=set()
    )
    assert found is not None
    assert found["id"] == "p_x"


def test_find_returns_none_with_no_match(operator_link_module):
    conn = _make_people_db()
    conn.execute("INSERT INTO people (id, canonical_name, created_at, updated_at) VALUES ('p_x', 'Alice', 0, 0)")
    conn.commit()
    found = operator_link_module.find_operator_by_identifier(
        conn, phones={"+15551234567"}, emails=set()
    )
    assert found is None


def test_find_picks_highest_overlap(operator_link_module):
    """When multiple rows match, pick the one with the most overlapping identifiers."""
    conn = _make_people_db()
    conn.executemany(
        "INSERT INTO people (id, canonical_name, created_at, updated_at) VALUES (?, ?, 0, 0)",
        [("p_a", "OldDup"), ("p_b", "RealOperator")],
    )
    conn.executemany(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) VALUES (?, ?, ?, ?)",
        [
            ("p_a", "phone", "+15551111111", "+15551111111"),
            ("p_b", "phone", "+15551111111", "+15551111111"),
            ("p_b", "phone", "+15552222222", "+15552222222"),
            ("p_b", "email", "real@op.com", "real@op.com"),
        ],
    )
    conn.commit()
    found = operator_link_module.find_operator_by_identifier(
        conn,
        phones={"+15551111111", "+15552222222"},
        emails={"real@op.com"},
    )
    assert found is not None
    assert found["id"] == "p_b"  # 3 overlaps vs 1


def test_find_prefers_existing_is_self(operator_link_module):
    conn = _make_people_db()
    conn.execute("INSERT INTO people (id, canonical_name, is_self, created_at, updated_at) VALUES ('p_self', 'Me', 1, 0, 0)")
    conn.execute("INSERT INTO people (id, canonical_name, created_at, updated_at) VALUES ('p_other', 'Other', 0, 0)")
    conn.execute(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) "
        "VALUES ('p_other', 'phone', '+15551234567', '+15551234567')"
    )
    conn.commit()
    found = operator_link_module.find_operator_by_identifier(
        conn, phones={"+15551234567"}, emails=set()
    )
    assert found["id"] == "p_self"


# ── find_dup_candidates ─────────────────────────────────────────────


def test_dup_candidates_excludes_target(operator_link_module):
    conn = _make_people_db()
    conn.executemany(
        "INSERT INTO people (id, canonical_name, created_at, updated_at) VALUES (?, ?, 0, 0)",
        [("p_target", "Target"), ("p_dup", "Dup")],
    )
    conn.executemany(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) VALUES (?, ?, ?, ?)",
        [
            ("p_target", "phone", "+15551234567", "+15551234567"),
            ("p_dup", "phone", "+15551234567", "+15551234567"),
        ],
    )
    conn.commit()
    dups = operator_link_module.find_dup_candidates(
        conn,
        phones={"+15551234567"},
        emails=set(),
        target_id="p_target",
    )
    ids = {d["id"] for d in dups}
    assert "p_target" not in ids
    assert "p_dup" in ids


def test_dup_candidates_skips_is_self_rows(operator_link_module):
    conn = _make_people_db()
    conn.execute("INSERT INTO people (id, canonical_name, is_self, created_at, updated_at) VALUES ('p_self', 'Me', 1, 0, 0)")
    conn.execute(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) "
        "VALUES ('p_self', 'phone', '+15551234567', '+15551234567')"
    )
    conn.commit()
    dups = operator_link_module.find_dup_candidates(
        conn,
        phones={"+15551234567"},
        emails=set(),
        target_id="p_target_doesnt_exist",
    )
    assert dups == []
