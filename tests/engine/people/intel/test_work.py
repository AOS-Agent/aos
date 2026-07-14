"""Tests for the Work System signal adapter.

Builds tiny fixture qareen.db SQLite databases in tmp_path and verifies
the adapter extracts person mentions correctly, handles optional tables
and columns gracefully, caps contexts, and respects stopword / short-name
filters.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.engine.people.intel.sources.work import WorkAdapter


# ── fixture builders ─────────────────────────────────────────────────


def _build_full_db(db_path: Path) -> None:
    """Build a qareen.db with tasks + projects, matching the real schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT,
            created_at TEXT
        );
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            goal TEXT,
            status TEXT,
            created_at TEXT
        );
        """
    )
    cur.executemany(
        "INSERT INTO tasks (id, title, description, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                "t1",
                "Call Alice Smith about budget",
                "Follow up from meeting",
                "open",
                "2026-04-01T10:00:00",
            ),
            (
                "t2",
                "Review PR",
                "Alice Smith's work needs review",
                "open",
                "2026-04-02T10:00:00",
            ),
            (
                "t3",
                "Misc admin",
                "Nothing to do with people",
                "open",
                "2026-04-03T10:00:00",
            ),
            (
                "t4",
                "Bob Jones onboarding",
                None,  # intentional NULL description
                "open",
                "2026-04-04T10:00:00",
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO projects (id, title, description, goal, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                "p1",
                "Client work for Alice Smith",
                "",
                "Ship by Q2",
                "active",
                "2026-03-15T10:00:00",
            ),
            (
                "p2",
                "Internal tooling",
                "",
                "",
                "active",
                "2026-03-16T10:00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()


def _build_minimal_db(db_path: Path) -> None:
    """DB with ONLY tasks (no projects, goals, inbox, friction_log)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks (id, title, description, created_at) VALUES (?, ?, ?, ?)",
        ("t1", "Ping Alice Smith", "", "2026-04-01T10:00:00"),
    )
    conn.commit()
    conn.close()


def _build_title_only_db(db_path: Path) -> None:
    """DB where tasks has NO description column (only title)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks (id, title) VALUES (?, ?)",
        ("t1", "Catch up with Alice Smith"),
    )
    conn.commit()
    conn.close()


def _build_many_mentions_db(db_path: Path, n: int = 25) -> None:
    """DB with many tasks each mentioning the same person."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO tasks (id, title, description, created_at) VALUES (?, ?, ?, ?)",
        [
            (f"t{i}", f"Task {i} about Alice Smith", "", "2026-04-01T10:00:00")
            for i in range(n)
        ],
    )
    conn.commit()
    conn.close()


# ── person_index shortcut ────────────────────────────────────────────


def _pindex(**named: str) -> dict[str, dict]:
    """Build a person_index {id: {name: ..., ...}} from keyword args."""
    return {
        pid: {"name": name, "phones": [], "emails": [], "wa_jids": []}
        for pid, name in named.items()
    }


# ── Tests ────────────────────────────────────────────────────────────


def test_is_available_true_false(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    adapter = WorkAdapter(db_path=str(db))
    assert adapter.is_available() is False

    _build_full_db(db)
    assert WorkAdapter(db_path=str(db)).is_available() is True


def test_extract_returns_dict(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    assert isinstance(result, dict)


def test_matches_in_task_title_and_description(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    assert "p_alice" in result
    signal = result["p_alice"].mentions[0]
    # Snippet sources should include both a title-matched task and a
    # description-matched task.
    files = {c["file"] for c in signal.mention_contexts}
    assert "tasks:t1" in files
    assert "tasks:t2" in files


def test_work_task_mentions_counted(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    signal = result["p_alice"].mentions[0]
    # 2 tasks (t1, t2) + 1 project (p1) = 3 minimum
    assert signal.work_task_mentions >= 3
    assert signal.total_mentions == signal.work_task_mentions
    assert signal.daily_log_mentions == 0
    assert signal.session_mentions == 0


def test_matches_concatenated_canonical_name(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    # Canonical name in camelCase — should match "Alice Smith" via the
    # spaced variant.
    result = adapter.extract_all(_pindex(p_alice="AliceSmith"))
    assert "p_alice" in result
    assert result["p_alice"].mentions[0].total_mentions >= 3


def test_skips_null_descriptions(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)  # t4 has NULL description
    adapter = WorkAdapter(db_path=str(db))
    # Should not crash — Bob Jones is only in the title of t4.
    result = adapter.extract_all(_pindex(p_bob="Bob Jones"))
    assert "p_bob" in result
    assert result["p_bob"].mentions[0].total_mentions >= 1


def test_skips_short_names(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT, created_at TEXT);
        INSERT INTO tasks VALUES ('t1', 'Kim was here', '', '2026-04-01');
        """
    )
    conn.commit()
    conn.close()
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_short="Kim"))
    assert "p_short" not in result


def test_skips_stopword_names(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT, created_at TEXT);
        INSERT INTO tasks VALUES ('t1', 'Work on the thing', '', '2026-04-01');
        """
    )
    conn.commit()
    conn.close()
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_work="Work"))
    assert "p_work" not in result


def test_unmatched_person_absent(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(
        _pindex(p_alice="Alice Smith", p_zed="Zed Xander")
    )
    assert "p_alice" in result
    assert "p_zed" not in result


def test_mention_contexts_capped_at_20(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_many_mentions_db(db, n=25)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    signal = result["p_alice"].mentions[0]
    # Total mentions counts all 25 regex matches.
    assert signal.total_mentions == 25
    # But contexts are capped at 20.
    assert len(signal.mention_contexts) == 20


def test_handles_missing_optional_tables(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_minimal_db(db)  # only tasks, no projects/goals/inbox/friction_log
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    assert "p_alice" in result
    assert result["p_alice"].mentions[0].total_mentions >= 1


def test_handles_missing_optional_columns(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_title_only_db(db)  # tasks has NO description column
    adapter = WorkAdapter(db_path=str(db))
    # Should not crash, and should still pick up title matches.
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    assert "p_alice" in result
    assert result["p_alice"].mentions[0].total_mentions >= 1


def test_extract_returns_empty_when_db_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    adapter = WorkAdapter(db_path=str(missing))
    assert adapter.extract_all(_pindex(p_alice="Alice Smith")) == {}


def test_snippet_includes_context_around_match(tmp_path: Path) -> None:
    db = tmp_path / "qareen.db"
    _build_full_db(db)
    adapter = WorkAdapter(db_path=str(db))
    result = adapter.extract_all(_pindex(p_alice="Alice Smith"))
    signal = result["p_alice"].mentions[0]
    assert len(signal.mention_contexts) > 0
    for ctx in signal.mention_contexts:
        assert "alice" in ctx["snippet"].lower()
        assert set(ctx.keys()) == {"file", "snippet", "date"}
        assert ctx["file"].startswith(("tasks:", "projects:"))
