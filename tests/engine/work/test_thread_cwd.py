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
