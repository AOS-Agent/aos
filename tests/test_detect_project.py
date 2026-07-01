"""
Tests for engine.detect_project_from_cwd — DB-driven project detection.

detect_project_from_cwd decides which project a task belongs to based on the
working directory. It is DB-driven: it matches cwd against each project's
`path` column in qareen.db (longest match wins), and only falls back to the
legacy hardcoded _PROJECT_DIRS map for projects that have no path set.

These tests point engine.DB_PATH at a throwaway SQLite file so nothing touches
~/.aos/. Real, on-disk directories are used as project paths (via tmp_path) so
that pathlib .resolve() has something to resolve.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# conftest.py already puts core/engine/work on sys.path; be explicit too.
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "engine" / "work"))

import engine  # noqa: E402


def _write_projects(path: Path, projects: list[tuple[str, str | None]]) -> None:
    """(Re)create a minimal qareen-shaped `projects` table and insert rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE IF EXISTS projects")
    conn.execute(
        "CREATE TABLE projects ("
        "  id TEXT PRIMARY KEY,"
        "  title TEXT NOT NULL,"
        "  path TEXT"
        ")"
    )
    conn.executemany(
        "INSERT INTO projects (id, title, path) VALUES (?, ?, ?)",
        [(pid, pid, ppath) for pid, ppath in projects],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def seed(tmp_path, monkeypatch):
    """Point engine at a temp qareen.db; return a callable to fill projects.

    Usage:
        def test_x(tmp_path, seed):
            proj = tmp_path / "repo"; proj.mkdir()
            seed([("my-proj", str(proj))])
            assert engine.detect_project_from_cwd(str(proj)) == "my-proj"
    """
    db_path = tmp_path / "qareen.db"
    monkeypatch.setattr(engine, "DB_PATH", db_path)
    monkeypatch.setattr(engine, "_conn", None)  # drop any cached real connection

    def _seed(projects):
        _write_projects(db_path, projects)
        engine._conn = None  # force the next _db() to open the temp file
        return db_path

    yield _seed

    # Teardown: close and clear the cached connection so it can't leak.
    try:
        if engine._conn is not None:
            engine._conn.close()
    finally:
        engine._conn = None


# ---------------------------------------------------------------------------
# Primary DB-driven matching
# ---------------------------------------------------------------------------

def test_exact_path_match(tmp_path, seed):
    proj = tmp_path / "quran-tools"
    proj.mkdir()
    seed([("quran-garden-ios", str(proj))])

    assert engine.detect_project_from_cwd(str(proj)) == "quran-garden-ios"


def test_nested_subdir_match(tmp_path, seed):
    proj = tmp_path / "quran-tools"
    deep = proj / "ios" / "Sources"
    deep.mkdir(parents=True)
    seed([("quran-garden-ios", str(proj))])

    assert engine.detect_project_from_cwd(str(deep)) == "quran-garden-ios"


def test_longest_match_wins_when_projects_nest(tmp_path, seed):
    """A nested project must beat its parent when both contain cwd."""
    parent = tmp_path / "monorepo"
    child = parent / "apps" / "ios"
    child.mkdir(parents=True)
    seed([("monorepo", str(parent)), ("ios-app", str(child))])

    # cwd inside the child -> the deeper (longer) path must win.
    assert engine.detect_project_from_cwd(str(child / "deep")) == "ios-app"
    # cwd only inside the parent -> parent wins.
    assert engine.detect_project_from_cwd(str(parent / "docs")) == "monorepo"


def test_tilde_and_symlink_form_resolves(tmp_path, seed):
    """A project stored under a symlinked path is matched via the resolved form.

    This mirrors the real setup where projects live at
    /Volumes/AOS-X/project/... but are reached through the ~/project symlink.
    """
    real = tmp_path / "real-project-root"
    real.mkdir()
    link = tmp_path / "linked"
    os.symlink(real, link)

    # Store under the SYMLINK path; query with the REAL path.
    seed([("linked-proj", str(link))])
    assert engine.detect_project_from_cwd(str(real)) == "linked-proj"

    # Reverse: store the REAL path; query via the symlink.
    seed([("linked-proj", str(real))])
    assert engine.detect_project_from_cwd(str(link)) == "linked-proj"


def test_no_match_returns_none(tmp_path, seed):
    proj = tmp_path / "some-project"
    proj.mkdir()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    seed([("some-project", str(proj))])

    assert engine.detect_project_from_cwd(str(outside)) is None


def test_empty_and_null_paths_are_ignored(tmp_path, seed):
    proj = tmp_path / "has-path"
    proj.mkdir()
    seed([
        ("no-path", None),
        ("blank-path", "   "),
        ("has-path", str(proj)),
    ])

    assert engine.detect_project_from_cwd(str(proj)) == "has-path"


def test_db_pass_beats_legacy_map(tmp_path, seed):
    """A DB path match takes precedence over the legacy name map.

    'aos' is also in _PROJECT_DIRS, but a real DB path must win.
    """
    proj = tmp_path / "aos-checkout"
    proj.mkdir()
    seed([("aos", str(proj))])

    assert engine.detect_project_from_cwd(str(proj)) == "aos"


# ---------------------------------------------------------------------------
# Legacy fallback + defensiveness
# ---------------------------------------------------------------------------

def test_falls_back_to_legacy_map_when_db_has_no_paths(tmp_path, seed):
    """Projects with no path fall through to the hardcoded _PROJECT_DIRS map."""
    seed([("aos", None)])  # DB present but no usable paths

    # A dir literally named 'aos' matches the legacy name map.
    assert engine.detect_project_from_cwd(str(Path.home() / "aos")) == "aos"


def test_missing_db_does_not_crash(tmp_path, monkeypatch):
    """A missing DB must not raise — detection falls through gracefully."""
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "does-not-exist.db")
    monkeypatch.setattr(engine, "_conn", None)
    try:
        result = engine.detect_project_from_cwd(str(tmp_path / "whatever"))
        assert result is None
    finally:
        if engine._conn is not None:
            engine._conn.close()
        engine._conn = None
