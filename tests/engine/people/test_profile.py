"""Tests for the deterministic profile compiler.

All inputs are FABRICATED. No real operator data in this file.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from core.engine.people.profile import (
    Profile,
    compile_all,
    compile_profile,
    persist,
    render_markdown,
    slug_for,
)


# ── Fixture: minimal in-memory schema covering both DBs via ATTACH ──


def _bootstrap_db(tmp_path: Path) -> sqlite3.Connection:
    people_path = tmp_path / "people.db"
    comms_path = tmp_path / "comms.db"

    p = sqlite3.connect(str(people_path))
    p.row_factory = sqlite3.Row
    p.executescript("""
        CREATE TABLE people (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            nickname TEXT,
            importance INTEGER DEFAULT 3,
            is_self INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE aliases (
            alias TEXT PRIMARY KEY,
            person_id TEXT,
            type TEXT,
            priority INTEGER DEFAULT 0,
            created_at INTEGER
        );
        CREATE TABLE person_identifiers (
            person_id TEXT,
            type TEXT,
            value TEXT,
            normalized TEXT,
            source TEXT,
            PRIMARY KEY (person_id, type, value)
        );
        CREATE TABLE contact_metadata (
            person_id TEXT PRIMARY KEY,
            birthday TEXT, city TEXT, country TEXT,
            organization TEXT, job_title TEXT, how_met TEXT
        );
        CREATE TABLE relationships (
            person_a_id TEXT, person_b_id TEXT, type TEXT, subtype TEXT,
            strength REAL, source TEXT, context TEXT, since TEXT,
            created_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE relationship_state (
            person_id TEXT PRIMARY KEY,
            last_interaction_at INTEGER,
            avg_days_between REAL,
            interaction_count_30d INTEGER,
            days_since_contact INTEGER,
            trajectory TEXT
        );
        CREATE TABLE person_classification (
            person_id TEXT PRIMARY KEY,
            tier TEXT, model TEXT, run_id TEXT, created_at INTEGER
        );
        CREATE TABLE signal_store (
            person_id TEXT, source_name TEXT, signals_json TEXT, extracted_at INTEGER,
            PRIMARY KEY (person_id, source_name)
        );
        CREATE TABLE interactions (
            id TEXT PRIMARY KEY, person_id TEXT, occurred_at INTEGER,
            channel TEXT, summary TEXT
        );
    """)
    p.commit()
    p.close()

    c = sqlite3.connect(str(comms_path))
    c.executescript("""
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, channel TEXT, direction TEXT,
            sender_id TEXT, recipient_id TEXT, content TEXT, timestamp TEXT,
            person_id TEXT, conversation_id TEXT
        );
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, channel TEXT, person_id TEXT, name TEXT,
            status TEXT, last_message_at TEXT, message_count INTEGER
        );
    """)
    c.commit()
    c.close()

    # Open combined connection (people + ATTACH comms)
    conn = sqlite3.connect(str(people_path))
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{comms_path}' AS c")
    return conn


def _seed_person(conn, pid, name, **kwargs):
    cols = ["id", "canonical_name", "importance", "created_at", "updated_at"]
    vals = [pid, name, kwargs.get("importance", 3), 0, 0]
    for k in ("display_name", "first_name", "last_name", "is_self"):
        if k in kwargs:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ",".join("?" * len(vals))
    conn.execute(
        f"INSERT INTO people ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )


# ── Tests ────────────────────────────────────────────────────────────


def test_compile_minimal_profile(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Alice Kumar", importance=2)
    conn.commit()

    profile = compile_profile("p_1", conn=conn)
    assert profile is not None
    assert profile.basics["canonical_name"] == "Alice Kumar"
    assert profile.basics["importance"] == 2
    assert profile.identifiers == {}
    assert profile.comms == {}


def test_compile_returns_none_for_missing_person(tmp_path):
    conn = _bootstrap_db(tmp_path)
    assert compile_profile("p_nonexistent", conn=conn) is None


def test_compile_full_profile(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Alice Kumar", importance=1, display_name="Ali")
    _seed_person(conn, "p_2", "Sam Taylor", importance=1)
    conn.executemany(
        "INSERT INTO aliases (alias, person_id, type, priority) VALUES (?, ?, ?, ?)",
        [("ali", "p_1", "short_name", 1), ("alice", "p_1", "alt", 0)],
    )
    conn.executemany(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) VALUES (?, ?, ?, ?)",
        [
            ("p_1", "phone", "+15551111111", "+15551111111"),
            ("p_1", "email", "alice@example.com", "alice@example.com"),
        ],
    )
    conn.execute(
        "INSERT INTO contact_metadata (person_id, birthday, city, organization) "
        "VALUES (?, ?, ?, ?)",
        ("p_1", "0000-06-15", "Toronto", "TestCorp"),
    )
    conn.execute(
        "INSERT INTO relationships (person_a_id, person_b_id, type, subtype, strength, context) "
        "VALUES (?, ?, 'family', 'sibling', 0.9, 'Sister')",
        ("p_1", "p_2"),
    )
    conn.execute(
        "INSERT INTO relationship_state (person_id, last_interaction_at, avg_days_between, "
        "interaction_count_30d, days_since_contact, trajectory) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("p_1", int(time.time()) - 86400, 7.5, 12, 1, "stable"),
    )
    conn.execute(
        "INSERT INTO person_classification (person_id, tier, run_id, created_at) "
        "VALUES (?, 'core', 'r1', ?)",
        ("p_1", int(time.time())),
    )
    sig = {
        "person_name": "Alice Kumar",
        "communication": [{
            "channel": "imessage",
            "total_messages": 1234,
            "avg_message_length": 22.5,
            "response_latency_median": 0.5,
            "late_night_pct": 0.4,
            "evening_pct": 0.3,
            "business_hours_pct": 0.2,
        }],
    }
    conn.execute(
        "INSERT INTO signal_store (person_id, source_name, signals_json, extracted_at) "
        "VALUES (?, 'apple_messages', ?, ?)",
        ("p_1", json.dumps(sig), int(time.time())),
    )
    # comms.db rows
    conn.execute(
        "INSERT INTO c.messages (id, channel, direction, person_id, content, timestamp) "
        "VALUES ('m1', 'imessage', 'inbound', 'p_1', 'hi', '2026-04-08T10:00:00')"
    )
    conn.execute(
        "INSERT INTO c.messages (id, channel, direction, person_id, content, timestamp) "
        "VALUES ('m2', 'imessage', 'outbound', 'p_1', 'yo', '2026-04-09T10:00:00')"
    )
    conn.commit()

    profile = compile_profile("p_1", conn=conn)
    assert profile is not None
    assert profile.basics["display_name"] == "Ali"
    assert {a["alias"] for a in profile.aliases} == {"ali", "alice"}
    assert profile.identifiers["phone"] == ["+15551111111"]
    assert profile.metadata["birthday"] == "0000-06-15"
    assert profile.metadata["city"] == "Toronto"
    assert profile.classification["tier"] == "core"
    assert len(profile.relationships) == 1
    assert profile.relationships[0]["other_id"] == "p_2"
    assert profile.relationships[0]["other_name"] == "Sam Taylor"
    assert profile.relationship_state["trajectory"] == "stable"
    assert profile.signals["sources"] == ["apple_messages"]
    assert profile.comms["total_messages"] == 2
    assert profile.comms["channels"]["imessage"]["inbound"] == 1
    assert profile.comms["channels"]["imessage"]["outbound"] == 1


def test_render_markdown_contains_key_sections(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Alice Kumar", importance=1)
    conn.execute(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) "
        "VALUES ('p_1', 'phone', '+15551111111', '+15551111111')"
    )
    conn.commit()

    md = render_markdown(compile_profile("p_1", conn=conn))
    assert 'title: "Alice Kumar"' in md
    assert "type: person" in md
    assert "inner-circle" in md
    assert "## Reach" in md
    assert "+15551111111" in md
    assert md.endswith("\n")


def test_slug_for_handles_special_chars(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Mr. O'Brien-Smith")
    conn.commit()
    profile = compile_profile("p_1", conn=conn)
    s = slug_for(profile)
    assert " " not in s
    assert s.islower()
    assert len(s) > 0


def test_persist_writes_profile_versions_and_vault(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Test Person", importance=2)
    conn.commit()

    vault_dir = tmp_path / "vault" / "people"
    profile = compile_profile("p_1", conn=conn)
    path = persist(profile, conn, vault_dir=vault_dir)

    assert path is not None
    assert Path(path).exists()
    assert "Test Person" in Path(path).read_text()

    rows = conn.execute("SELECT * FROM profile_versions WHERE person_id='p_1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["model"] == "deterministic"
    assert json.loads(rows[0]["profile_json"])["basics"]["canonical_name"] == "Test Person"


def test_compile_all_filters_by_importance(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "InnerOne", importance=1)
    _seed_person(conn, "p_2", "InnerTwo", importance=2)
    _seed_person(conn, "p_3", "Outer", importance=3)
    conn.commit()

    counts = compile_all(
        conn=conn,
        only_importance_at_most=2,
        write_vault=False,
        trigger="test",
    )
    assert counts["compiled"] == 2
    assert counts["errors"] == 0


def test_compile_all_filters_by_tier(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_a", "CoreA", importance=3)
    _seed_person(conn, "p_b", "ActiveB", importance=3)
    _seed_person(conn, "p_c", "Dormant", importance=3)
    conn.executemany(
        "INSERT INTO person_classification (person_id, tier, run_id, created_at) "
        "VALUES (?, ?, 'r1', 0)",
        [("p_a", "core"), ("p_b", "active"), ("p_c", "dormant")],
    )
    conn.commit()

    counts = compile_all(
        conn=conn,
        only_tiers=["core", "active"],
        write_vault=False,
        trigger="test",
    )
    assert counts["compiled"] == 2


def test_compile_skips_archived(tmp_path):
    conn = _bootstrap_db(tmp_path)
    _seed_person(conn, "p_1", "Alive", importance=1)
    _seed_person(conn, "p_2", "Archived", importance=1)
    conn.execute("UPDATE people SET is_archived=1 WHERE id='p_2'")
    conn.commit()

    counts = compile_all(conn=conn, only_importance_at_most=1, write_vault=False)
    assert counts["compiled"] == 1
