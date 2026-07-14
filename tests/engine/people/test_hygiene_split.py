"""Tests for canonical name hygiene — Phase 6.1 splitter + scanner.

All test inputs are FABRICATED or already-public conventional Arabic name
forms (titles + connectors). No real operator data is in this file.
"""
from __future__ import annotations

import sqlite3

import pytest

from core.engine.people.hygiene import (
    HygieneEngine,
    split_concatenated_name,
)


# ── Pure helper: split_concatenated_name ─────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Happy path — CamelCase Latin
        ("AbdusSamadRashid", "Abdus Samad Rashid"),
        ("KhaleeqUrRehman", "Khaleeq Ur Rehman"),
        ("MuhammadFurqanAli", "Muhammad Furqan Ali"),
        (
            "QariMuhammadYahyaRasoolNagriAlBalochi",
            "Qari Muhammad Yahya Rasool Nagri Al Balochi",
        ),
        # Dot-joined title
        ("Dr.BadarUlIslam", "Dr. Badar Ul Islam"),
        ("Dr.FarazUlHaq", "Dr. Faraz Ul Haq"),
        # Rejected: no transition
        ("Ahmed", None),
        ("AHMED", None),
        # Rejected: already has whitespace
        ("Ahmed Ali", None),
        ("Abdus Samad Rashid", None),
        # Rejected: slash compound
        ("AyeshaCOUSIN/MICHIGAN", None),
        ("Omar/OusamaUmrahTrip2025", None),
        # Rejected: ALLCAPS run
        ("SALTAccountingTeam", None),
        ("ZainAyub/ABERDEEN", None),
        # Rejected: non-ASCII (Arabic)
        ("\u0623\u062d\u0645\u062f\u0637\u0644\u0627\u0644\u0627\u0644\u0639\u0631\u0641\u062c", None),
        # Rejected: empty / None / too short
        ("", None),
        (None, None),
        ("Ab", None),
        # Rejected: contains digits
        ("Abdul3Ahmad", None),
    ],
)
def test_split_concatenated_name(raw, expected):
    assert split_concatenated_name(raw) == expected


def test_split_idempotent_on_clean_output():
    cleaned = split_concatenated_name("AbdusSamadRashid")
    # Re-running on the cleaned form must return None (already spaced)
    assert split_concatenated_name(cleaned) is None


# ── Integration: Tier 1 fix in run_tier1_fixes() ─────────────────────


def _bootstrap_db() -> sqlite3.Connection:
    """Minimal in-memory people DB for hygiene tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE people (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            nickname TEXT,
            importance INTEGER DEFAULT 3,
            privacy_level INTEGER DEFAULT 1,
            profile_version INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            golden_record_at INTEGER,
            merge_target_id TEXT
        );
        CREATE TABLE person_identifiers (
            person_id TEXT,
            type TEXT,
            value TEXT,
            normalized TEXT,
            PRIMARY KEY (person_id, type, value)
        );
        CREATE TABLE aliases (
            alias TEXT PRIMARY KEY,
            person_id TEXT,
            group_id TEXT,
            type TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            created_at INTEGER
        );
        CREATE TABLE hygiene_queue (
            id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            person_a_id TEXT,
            person_b_id TEXT,
            confidence REAL DEFAULT 0.0,
            reason TEXT,
            proposed_data TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            resolved_at INTEGER
        );
        CREATE TABLE interactions (
            person_id TEXT, occurred_at INTEGER
        );
        CREATE TABLE contact_metadata (
            person_id TEXT PRIMARY KEY,
            organization TEXT, birthday TEXT, city TEXT
        );
    """)
    return conn


def _seed_person(conn, pid, canonical, display=None):
    conn.execute(
        "INSERT INTO people (id, canonical_name, display_name, first_name, last_name, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 0)",
        (pid, canonical, display, canonical, ""),
    )


def test_run_tier1_splits_dirty_names():
    conn = _bootstrap_db()
    _seed_person(conn, "p_1", "AbdusSamadRashid")
    _seed_person(conn, "p_2", "Dr.BadarUlIslam")
    _seed_person(conn, "p_3", "KhaleeqUrRehman")
    _seed_person(conn, "p_4", "Ahmed Ali")  # already clean
    _seed_person(conn, "p_5", "AyeshaCOUSIN/MICHIGAN")  # rejected
    conn.commit()

    eng = HygieneEngine(conn=conn)
    counts = eng.run_tier1_fixes()
    assert counts["names_split"] == 3

    rows = {
        r["id"]: r["canonical_name"]
        for r in conn.execute("SELECT id, canonical_name FROM people").fetchall()
    }
    assert rows["p_1"] == "Abdus Samad Rashid"
    assert rows["p_2"] == "Dr. Badar Ul Islam"
    assert rows["p_3"] == "Khaleeq Ur Rehman"
    assert rows["p_4"] == "Ahmed Ali"  # untouched
    assert rows["p_5"] == "AyeshaCOUSIN/MICHIGAN"  # untouched

    aliases = {
        r["alias"]: r["type"]
        for r in conn.execute("SELECT alias, type FROM aliases").fetchall()
    }
    assert aliases["abdussamadrashid"] == "pre_split"
    assert aliases["dr.badarulislam"] == "pre_split"
    assert aliases["khaleequrrehman"] == "pre_split"


def test_split_updates_first_last_and_display_name():
    conn = _bootstrap_db()
    # display_name tracks canonical → should follow the split
    _seed_person(conn, "p_1", "AbdusSamadRashid", display="AbdusSamadRashid")
    # display_name was operator-set differently → should NOT change
    _seed_person(conn, "p_2", "MuhammadFurqanAli", display="Furqan")
    conn.commit()

    eng = HygieneEngine(conn=conn)
    eng.run_tier1_fixes()

    p1 = conn.execute("SELECT * FROM people WHERE id='p_1'").fetchone()
    assert p1["canonical_name"] == "Abdus Samad Rashid"
    assert p1["display_name"] == "Abdus Samad Rashid"
    assert p1["first_name"] == "Abdus"
    assert p1["last_name"] == "Samad Rashid"

    p2 = conn.execute("SELECT * FROM people WHERE id='p_2'").fetchone()
    assert p2["canonical_name"] == "Muhammad Furqan Ali"
    assert p2["display_name"] == "Furqan"  # preserved
    assert p2["first_name"] == "Muhammad"
    assert p2["last_name"] == "Furqan Ali"


def test_split_idempotent_across_runs():
    conn = _bootstrap_db()
    _seed_person(conn, "p_1", "AbdusSamadRashid")
    conn.commit()

    eng = HygieneEngine(conn=conn)
    first = eng.run_tier1_fixes()
    second = eng.run_tier1_fixes()
    assert first["names_split"] == 1
    assert second["names_split"] == 0

    # Alias inserted exactly once
    n = conn.execute(
        "SELECT COUNT(*) FROM aliases WHERE type='pre_split'"
    ).fetchone()[0]
    assert n == 1


# ── Integration: scan_dirty_names ────────────────────────────────────


def test_scan_dirty_names_classifies():
    conn = _bootstrap_db()
    _seed_person(conn, "p_slash", "Omar/OusamaUmrahTrip2025")
    _seed_person(conn, "p_caps", "AyeshaCOUSIN")
    _seed_person(conn, "p_arabic", "\u0623\u062d\u0645\u062f\u0637\u0644\u0627\u0644\u0639")  # 9 chars
    _seed_person(conn, "p_clean", "Ahmed Ali")
    conn.commit()

    eng = HygieneEngine(conn=conn)
    issues = eng.scan_dirty_names()
    by_pid = {i["person_a_id"]: i["reason"] for i in issues}

    assert "p_clean" not in by_pid
    assert by_pid["p_slash"].startswith("slash_compound")
    assert by_pid["p_caps"].startswith("allcaps_tag")
    assert by_pid["p_arabic"].startswith("arabic_concat")
    for issue in issues:
        assert issue["action_type"] == "rename_review"


def test_scan_all_writes_rename_review_to_queue_and_dedupes():
    conn = _bootstrap_db()
    _seed_person(conn, "p_slash", "Omar/Ousama")
    conn.commit()

    eng = HygieneEngine(conn=conn)
    eng.scan_all()
    eng.scan_all()  # second run must not duplicate

    rows = conn.execute(
        "SELECT action_type, person_a_id FROM hygiene_queue WHERE status='pending'"
    ).fetchall()
    rename_rows = [r for r in rows if r["action_type"] == "rename_review"]
    assert len(rename_rows) == 1
    assert rename_rows[0]["person_a_id"] == "p_slash"
