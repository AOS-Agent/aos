"""Tests for cwd → project detection, the live path.

``detect_project_from_cwd`` decides which project a task belongs to, and — as
of v0.7.7 — which project a SessionEnd's thread belongs to. It is DB-driven: it
matches cwd against each project's ``path`` column in work.db, **longest match
wins**, so a project nested inside another beats its parent.

These tests used to drive the predecessor flat work module (``import engine``),
which v0.7.7 deleted. They now drive ``backend.py``, the engine the CLI and the
hooks actually call, through the ``work_env`` fixture's throwaway AOS_WORK_DB —
so they cover the code that runs rather than a parallel copy of it. Two
behaviours the old module had are deliberately not carried over: a hardcoded
``_PROJECT_DIRS`` name map (a list the filesystem already declares), and
first-match-wins path resolution, which made the answer depend on row order.

Real, on-disk directories are used as project paths (via tmp_path) so that
pathlib .resolve() has something to resolve.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# conftest.py already puts core/engine/work on sys.path; be explicit too.
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "engine" / "work"))


@pytest.fixture()
def seed(work_env):
    """Fill the isolated work.db's `projects` table; return the engine.

    Paths are written with raw SQL rather than ``add_project`` because ``path``
    has no parameter on the public API — it is set by the project layer, not by
    task creation.
    """
    eng = work_env["engine"]

    def _seed(projects):
        conn = sqlite3.connect(str(work_env["db_path"]))
        conn.execute("DELETE FROM projects")
        conn.executemany(
            "INSERT INTO projects (id, title, path) VALUES (?, ?, ?)",
            [(pid, pid, ppath) for pid, ppath in projects],
        )
        conn.commit()
        conn.close()
        return eng

    return _seed


# ---------------------------------------------------------------------------
# DB-driven matching
# ---------------------------------------------------------------------------

def test_exact_path_match(tmp_path, seed):
    proj = tmp_path / "quran-tools"
    proj.mkdir()
    eng = seed([("quran-garden-ios", str(proj))])

    assert eng.detect_project_from_cwd(str(proj)) == "quran-garden-ios"


def test_nested_subdir_match(tmp_path, seed):
    proj = tmp_path / "quran-tools"
    deep = proj / "ios" / "Sources"
    deep.mkdir(parents=True)
    eng = seed([("quran-garden-ios", str(proj))])

    assert eng.detect_project_from_cwd(str(deep)) == "quran-garden-ios"


def test_longest_match_wins_when_projects_nest(tmp_path, seed):
    """A nested project must beat its parent when both contain cwd.

    Both orderings are seeded because the bug this guards against is row-order
    dependence: whichever project the adapter happened to list first used to
    win, so the same directory could resolve differently on two machines.
    """
    parent = tmp_path / "monorepo"
    child = parent / "apps" / "ios"
    child.mkdir(parents=True)

    for rows in (
        [("monorepo", str(parent)), ("ios-app", str(child))],
        [("ios-app", str(child)), ("monorepo", str(parent))],
    ):
        eng = seed(rows)
        assert eng.detect_project_from_cwd(str(child / "deep")) == "ios-app"
        assert eng.detect_project_from_cwd(str(parent / "docs")) == "monorepo"


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
    eng = seed([("linked-proj", str(link))])
    assert eng.detect_project_from_cwd(str(real)) == "linked-proj"

    # Reverse: store the REAL path; query via the symlink.
    eng = seed([("linked-proj", str(real))])
    assert eng.detect_project_from_cwd(str(link)) == "linked-proj"


def test_no_match_returns_none(tmp_path, seed):
    proj = tmp_path / "some-project"
    proj.mkdir()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    eng = seed([("some-project", str(proj))])

    assert eng.detect_project_from_cwd(str(outside)) is None


def test_empty_and_null_paths_are_ignored(tmp_path, seed):
    proj = tmp_path / "has-path"
    proj.mkdir()
    eng = seed([
        ("no-path", None),
        ("blank-path", "   "),
        ("has-path", str(proj)),
    ])

    assert eng.detect_project_from_cwd(str(proj)) == "has-path"


def test_directory_named_after_a_pathless_project_still_matches(tmp_path, seed):
    """The fallback for a project with no `path` recorded: a directory whose
    leaf name IS the project id. This is the only name-based rule left — it is
    derived from the project rows, not from a hardcoded map of the operator's
    machine."""
    proj = tmp_path / "aos"
    proj.mkdir()
    eng = seed([("aos", None)])

    assert eng.detect_project_from_cwd(str(proj)) == "aos"


def test_empty_projects_table_does_not_crash(tmp_path, seed):
    eng = seed([])
    assert eng.detect_project_from_cwd(str(tmp_path / "whatever")) is None
