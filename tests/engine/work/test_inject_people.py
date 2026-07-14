"""Tests for `_build_people_section` in core/engine/work/inject_context.py.

The helper reads people.db directly, selects currently-relevant people
(tier in core/active/emerging, recent activity from signal_store), and
returns a markdown section for Chief's session-start context.

Contract under test:
  - Returns empty string when the database is missing
  - Returns empty string when required tables are missing
  - Returns a non-empty section containing the expected header and names
  - Excludes persons classified as dormant/unknown
  - Skips persons with no detectable last_interaction_date
  - Hard-caps output at 20 entries
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_DIR = REPO_ROOT / "core" / "engine" / "work"
sys.path.insert(0, str(WORK_DIR))
sys.path.insert(0, str(REPO_ROOT / "core" / "engine"))

import inject_context  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────


def _make_db(path: Path) -> None:
    """Create a minimal people.db with the tables needed by the helper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE people (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                canonical_name TEXT,
                first_name TEXT,
                last_name TEXT,
                is_archived INTEGER DEFAULT 0
            );

            CREATE TABLE person_classification (
                person_id TEXT PRIMARY KEY,
                tier TEXT NOT NULL,
                context_tags_json TEXT NOT NULL DEFAULT '[]',
                reasoning TEXT,
                model TEXT,
                run_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE signal_store (
                person_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                signals_json TEXT NOT NULL,
                extracted_at INTEGER NOT NULL,
                PRIMARY KEY (person_id, source_name)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_person(
    db: Path,
    pid: str,
    name: str,
    tier: str,
    *,
    tags: list | None = None,
    last_msg_days_ago: int | None = 1,
    channel: str = "imessage",
    total_messages: int = 50,
    archived: bool = False,
    extra_channels: list | None = None,
) -> None:
    """Insert one person + classification + signal row."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO people (id, display_name, canonical_name, is_archived) VALUES (?, ?, ?, ?)",
            (pid, name, name, 1 if archived else 0),
        )
        conn.execute(
            """INSERT INTO person_classification
                (person_id, tier, context_tags_json, run_id, created_at)
               VALUES (?, ?, ?, 'test-run', ?)""",
            (pid, tier, json.dumps(tags or []), int(time.time())),
        )
        if last_msg_days_ago is not None:
            last_date = (datetime.utcnow() - timedelta(days=last_msg_days_ago)).isoformat()
            comms = [
                {
                    "source": "test",
                    "channel": channel,
                    "total_messages": total_messages,
                    "last_message_date": last_date,
                }
            ]
            for extra in extra_channels or []:
                comms.append(
                    {
                        "source": "test",
                        "channel": extra,
                        "total_messages": 10,
                        "last_message_date": last_date,
                    }
                )
            signals = {"communication": comms}
            conn.execute(
                """INSERT INTO signal_store (person_id, source_name, signals_json, extracted_at)
                   VALUES (?, ?, ?, ?)""",
                (pid, "test", json.dumps(signals), int(time.time())),
            )
        conn.commit()
    finally:
        conn.close()


# ── Tests ────────────────────────────────────────────────────────────


def test_missing_db_returns_empty(tmp_path: Path) -> None:
    """Non-existent db path → empty string, never a crash."""
    result = inject_context._build_people_section(
        db_path=tmp_path / "nope.db"
    )
    assert result == ""


def test_missing_tables_returns_empty(tmp_path: Path) -> None:
    """DB exists but has no people table → empty string."""
    db = tmp_path / "people.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE irrelevant (x INTEGER)")
    conn.commit()
    conn.close()
    result = inject_context._build_people_section(db_path=db)
    assert result == ""


def test_three_relevant_people(tmp_path: Path) -> None:
    """All three fabricated people appear; dormant person is excluded."""
    db = tmp_path / "people.db"
    _make_db(db)
    _insert_person(
        db, "p1", "Alex Kumar", "core",
        tags=["family_nuclear", "colleague"],
        channel="imessage", total_messages=500, last_msg_days_ago=2,
        extra_channels=["phone"],
    )
    _insert_person(
        db, "p2", "Jordan Lee", "active",
        channel="whatsapp", total_messages=80, last_msg_days_ago=5,
    )
    _insert_person(
        db, "p3", "Sam Taylor", "emerging",
        channel="imessage", total_messages=20, last_msg_days_ago=1,
    )
    # Dormant — must NOT appear
    _insert_person(
        db, "p4", "Old Contact", "dormant",
        channel="email", total_messages=10, last_msg_days_ago=300,
    )

    result = inject_context._build_people_section(db_path=db)

    assert result, "expected non-empty section"
    assert "Today's Relevant People" in result
    assert "Alex Kumar" in result
    assert "Jordan Lee" in result
    assert "Sam Taylor" in result
    assert "Old Contact" not in result
    # Tier labels appear
    assert "core" in result
    assert "active" in result
    assert "emerging" in result
    # Context tags rendered
    assert "family_nuclear" in result
    # Days-since format
    assert "d ago" in result


def test_skips_persons_with_no_last_interaction(tmp_path: Path) -> None:
    """A classified person with no signal_store row should be skipped."""
    db = tmp_path / "people.db"
    _make_db(db)
    _insert_person(db, "p1", "Has Signals", "core", last_msg_days_ago=3)
    _insert_person(db, "p2", "No Signals", "active", last_msg_days_ago=None)

    result = inject_context._build_people_section(db_path=db)

    assert "Has Signals" in result
    assert "No Signals" not in result


def test_cap_at_twenty(tmp_path: Path) -> None:
    """Inserting 25 people yields at most 20 bullet entries."""
    db = tmp_path / "people.db"
    _make_db(db)
    for i in range(25):
        _insert_person(
            db,
            f"p{i}",
            f"Person {i:02d}",
            "active",
            total_messages=100 - i,  # density varies
            last_msg_days_ago=(i % 10) + 1,
        )

    result = inject_context._build_people_section(db_path=db)
    bullet_count = sum(1 for line in result.splitlines() if line.startswith("- **"))
    assert bullet_count == 20, f"expected 20 bullets, got {bullet_count}"
    assert "top 20" in result


def test_excludes_archived(tmp_path: Path) -> None:
    """is_archived = 1 → person is omitted even if tier is core."""
    db = tmp_path / "people.db"
    _make_db(db)
    _insert_person(db, "p1", "Hidden Person", "core", archived=True)
    _insert_person(db, "p2", "Visible Person", "active")

    result = inject_context._build_people_section(db_path=db)
    assert "Hidden Person" not in result
    assert "Visible Person" in result
