"""Thread-per-directory dedup regression tests (aos#223).

``get_or_create_thread_for_cwd`` backs the SessionEnd hook's "thread
continuity" step (session_close.py). Before this fix, ``find_thread_by_cwd``
was a permanent stub returning ``None`` ("the DB has no cwd column"), so
"find-or-create" was always "create": one new "Work in <dir>" thread per
qualifying SessionEnd, forever. ``~/aos`` is a release symlink whose target
name changes on every update, so its leaf directory name (and therefore the
auto-generated title) churns on every release even for the SAME logical
checkout. The live DB accumulated 4,635 such rows — all status='exploring',
spread across only 23 distinct titles, 0 ever promoted.

The fix restores a real ``threads.cwd`` column and makes ``find_thread_by_cwd``
actually look things up, so a directory gets at most one open thread ever;
re-running the hook links the existing thread instead of minting another.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations


def test_get_or_create_thread_for_cwd_creates_exactly_one_thread(work_env):
    eng = work_env["engine"]
    cwd = "/tmp/operator/aos"

    first = eng.get_or_create_thread_for_cwd(cwd, "session-1")
    second = eng.get_or_create_thread_for_cwd(cwd, "session-2")

    assert first["id"] == second["id"], (
        "a second SessionEnd for the same directory must reuse the thread, "
        "not mint another"
    )

    threads = [t for t in eng.get_all_threads() if t["title"] == first["title"]]
    assert len(threads) == 1, (
        f"expected exactly one thread for {cwd!r}, found {len(threads)}"
    )


def test_get_or_create_thread_for_cwd_survives_many_repeated_sessions(work_env):
    """The actual flood shape: N SessionEnds in the same directory over time."""
    eng = work_env["engine"]
    cwd = "/tmp/operator/aos"

    for i in range(10):
        eng.get_or_create_thread_for_cwd(cwd, f"session-{i}")

    matching = [t for t in eng.get_all_threads() if t["title"] == "Work in aos"]
    assert len(matching) == 1, (
        f"10 SessionEnds in the same directory produced {len(matching)} "
        f"threads, expected 1"
    )


def test_different_cwds_with_same_basename_stay_distinct(work_env):
    """Title alone (basename) is not a safe dedup key — two different
    directories can share a leaf name (seen in prod: 'Work in core' has hits
    from more than one checkout). The fix must key on the real cwd, not the
    derived title."""
    eng = work_env["engine"]

    a = eng.get_or_create_thread_for_cwd("/tmp/operator/repo-one/core", "s1")
    b = eng.get_or_create_thread_for_cwd("/tmp/operator/repo-two/core", "s2")

    assert a["id"] != b["id"]
    matching = [t for t in eng.get_all_threads() if t["title"] == "Work in core"]
    assert len(matching) == 2


def test_find_thread_by_cwd_none_before_any_session(work_env):
    eng = work_env["engine"]
    assert eng.find_thread_by_cwd("/tmp/operator/never-visited") is None


def test_find_thread_by_cwd_finds_after_create(work_env):
    eng = work_env["engine"]
    cwd = "/tmp/operator/aos"
    created = eng.add_thread("Work in aos", cwd=cwd)

    found = eng.find_thread_by_cwd(cwd)
    assert found is not None
    assert found["id"] == created["id"]


def test_second_call_links_session_without_crashing(work_env):
    """Regression guard: the adapter's link_session_to_thread wrote straight
    to a `sessions` table that does not exist in work.db (sessions/
    session_tasks are Qareen-owned, routed via _session_conn — see
    link_session_to_task). That path was unreachable while find_thread_by_cwd
    always returned None; fixing the lookup makes it reachable, so it must
    not explode with 'no such table: sessions'."""
    eng = work_env["engine"]
    cwd = "/tmp/operator/aos"

    eng.get_or_create_thread_for_cwd(cwd, "session-1")
    second = eng.get_or_create_thread_for_cwd(cwd, "session-2")
    assert second is not None


# ===========================================================================
# Project-keyed dedup (v0.7.7)
#
# cwd was the wrong key. One project is reachable under many directory names:
# three worktrees under <project>/.claude/worktrees/*, the ~/project symlink,
# and ~/aos — a release symlink whose target is ~/aos-releases/<version>, a
# different directory on every single update. Keying on the cwd string gave
# each of them its own thread, which is how the live DB reached 4,635 rows
# across 23 distinct titles. The key is the project the directory belongs to.
# ===========================================================================

import sqlite3


def _seed_project(work_env, project_id: str, path) -> None:
    """Give a project a `path` — there is no public parameter for it (the
    project layer owns that column, not task creation)."""
    conn = sqlite3.connect(str(work_env["db_path"]))
    conn.execute(
        "INSERT OR REPLACE INTO projects (id, title, path) VALUES (?, ?, ?)",
        (project_id, project_id, str(path)),
    )
    conn.commit()
    conn.close()


def _open_threads(eng):
    return [t for t in eng.get_all_threads() if t["status"] != "closed"]


def test_ten_session_ends_across_worktrees_and_the_release_target_make_one_thread(
    work_env, tmp_path
):
    """The done-when, in one test: 10 SessionEnds over the four spellings of a
    single checkout must leave at most one open thread."""
    eng = work_env["engine"]

    project_root = tmp_path / "project" / "aos"
    worktrees = [
        project_root / ".claude" / "worktrees" / slug
        for slug in ("feat-work-10x", "chore-single-node", "feat-telegram")
    ]
    release_target = tmp_path / "aos-releases" / "v0.7.7-abc1234"
    for d in (project_root, *worktrees, release_target):
        d.mkdir(parents=True, exist_ok=True)
    _seed_project(work_env, "aos", project_root)

    cwds = [str(project_root), *map(str, worktrees), str(release_target)]
    for i in range(10):
        eng.get_or_create_thread_for_cwd(cwds[i % len(cwds)], f"session-{i}")

    open_threads = _open_threads(eng)
    assert len(open_threads) <= 1, (
        f"10 SessionEnds across {len(cwds)} spellings of one project produced "
        f"{len(open_threads)} open threads: "
        f"{[(t['id'], t['title'], t['cwd']) for t in open_threads]}"
    )
    assert open_threads[0]["project"] == "aos"
    assert open_threads[0]["title"] == "Work in aos", (
        "the title must name the project, not whichever worktree happened to "
        "end a session first — it is the one stable name for all of them"
    )


def test_worktree_and_project_root_share_a_thread(work_env, tmp_path):
    eng = work_env["engine"]
    root = tmp_path / "project" / "quran-garden"
    wt = root / ".claude" / "worktrees" / "feat-x"
    wt.mkdir(parents=True)
    _seed_project(work_env, "quran-garden", root)

    a = eng.get_or_create_thread_for_cwd(str(root), "s1")
    b = eng.get_or_create_thread_for_cwd(str(wt), "s2")
    assert a["id"] == b["id"]


def test_release_symlink_target_joins_the_framework_project(work_env, tmp_path):
    """~/aos-releases/<version> is the resolved form of ~/aos. A new version
    directory every update is exactly what made this the worst offender in the
    live data (2,015 rows under one title)."""
    eng = work_env["engine"]
    root = tmp_path / "project" / "aos"
    root.mkdir(parents=True)
    _seed_project(work_env, "aos", root)

    old = tmp_path / "aos-releases" / "v0.7.1-bdd0739"
    new = tmp_path / "aos-releases" / "v0.7.7-dff5c0d"
    for d in (old, new):
        d.mkdir(parents=True)

    first = eng.get_or_create_thread_for_cwd(str(old), "s1")
    second = eng.get_or_create_thread_for_cwd(str(new), "s2")
    third = eng.get_or_create_thread_for_cwd(str(root), "s3")

    assert first["id"] == second["id"] == third["id"]
    assert len(_open_threads(eng)) == 1


def test_two_projects_keep_their_own_threads(work_env, tmp_path):
    """Dedup, not collapse: the fix must not merge distinct projects."""
    eng = work_env["engine"]
    a_root = tmp_path / "project" / "alpha"
    b_root = tmp_path / "project" / "beta"
    for d in (a_root, b_root):
        d.mkdir(parents=True)
    _seed_project(work_env, "alpha", a_root)
    _seed_project(work_env, "beta", b_root)

    a = eng.get_or_create_thread_for_cwd(str(a_root), "s1")
    b = eng.get_or_create_thread_for_cwd(str(b_root), "s2")
    assert a["id"] != b["id"]
    assert len(_open_threads(eng)) == 2


def test_a_closed_thread_is_not_resurrected(work_env, tmp_path):
    """Migration 115 closed 3,574 of these. A closed thread is a deliberate
    statement that the exploration is over — the next session starts fresh."""
    eng = work_env["engine"]
    root = tmp_path / "project" / "aos"
    root.mkdir(parents=True)
    _seed_project(work_env, "aos", root)

    first = eng.get_or_create_thread_for_cwd(str(root), "s1")
    eng.update_thread(first["id"], status="closed")

    second = eng.get_or_create_thread_for_cwd(str(root), "s2")
    assert second["id"] != first["id"]
    assert len(_open_threads(eng)) == 1


def test_an_operator_thread_for_the_project_is_reused_not_duplicated(work_env, tmp_path):
    """A promoted thread is still the project's open thread. Minting an auto
    thread beside it is how one project ends up with two."""
    eng = work_env["engine"]
    root = tmp_path / "project" / "aos"
    root.mkdir(parents=True)
    _seed_project(work_env, "aos", root)

    hand_written = eng.add_thread("Qren cutover night", cwd=None, project="aos")
    reused = eng.get_or_create_thread_for_cwd(str(root), "s1")

    assert reused["id"] == hand_written["id"]
    assert len(_open_threads(eng)) == 1


def test_unprojected_directory_still_falls_back_to_cwd(work_env, tmp_path):
    """No project resolves → the old behaviour, unchanged: one thread per
    directory. The fix narrows the key, it does not remove the fallback."""
    eng = work_env["engine"]
    scratch = tmp_path / "somewhere" / "unknown"
    scratch.mkdir(parents=True)

    a = eng.get_or_create_thread_for_cwd(str(scratch), "s1")
    b = eng.get_or_create_thread_for_cwd(str(scratch), "s2")
    assert a["id"] == b["id"]
    assert a["project"] is None
