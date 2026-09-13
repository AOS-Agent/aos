"""
Migration 125: close the cwd-keyed "Work in …" threads the new generator
can no longer reach.

v0.7.7 changes the SessionEnd thread key from the cwd string to the project
(`backend.get_or_create_thread_for_cwd`). That is the fix for the flood
migration 115 mopped up: one project answers to several directory names — three
worktrees, the ~/project symlink, and ~/aos, whose release target is renamed on
every update — so keying on cwd minted a thread per spelling. The live DB holds
4,639 `status='exploring'` rows across 23 distinct titles, 2,015 of them under
the single title "Work in v0.7.1-bdd0739", and exactly 2 threads were ever
promoted.

Migration 115 closed everything older than 7 days **at the time it ran**, and is
a one-time sweep: it will not run again, and the generator kept producing rows
behind it until this release. This migration is the second half — the one that
can be final, because the generator is fixed in the same release.

Why these rows are unreachable rather than merely untidy: an auto thread created
before this release has `project_id IS NULL` (only `promote_thread` ever set that
column). The new find-or-create looks up `threads.project_id` for any directory
that resolves to a tracked project, so it will never find one of these again —
it would create a project-keyed thread beside it and the old row would sit open
forever, in `work threads` and in every count, describing nothing.

Three conditions, all required:
  1. `status = 'exploring'` — a promoted or already-closed thread is untouched.
     The two promoted threads and the hand-written ones ("People DB intelligence
     gaps", "Qren cutover night…") do not match on condition 2 either.
  2. `title LIKE 'Work in %'` — the generated shape only, the same
     discriminator migration 115 used and `backend.AUTO_THREAD_PREFIX` now
     names in one place.
  3. `project_id IS NULL` — a thread that already knows its project is exactly
     what the new key reuses, so closing it would throw away the continuity this
     release is building. **No age window**: unlike 115 this is not guessing at
     staleness, it is closing rows whose lookup key no longer exists.

Closes, never deletes — `status='closed'` keeps the record of which directories
existed when, and one UPDATE reverses it. work.db is copied to
~/.aos/backups/pre-purge/ before the first write.

Cost of being wrong, stated plainly: the next session in a directory with no
tracked project creates one fresh thread instead of continuing a closed one.
That is one row, once, per directory — against 4,639 rows that describe a
directory name from a release that no longer exists.

Idempotent: check() passes once nothing matches all three conditions.
"""

from __future__ import annotations

DESCRIPTION = "Close the cwd-keyed 'Work in …' threads the project-keyed generator cannot reach"

import shutil
import sqlite3
import time
from pathlib import Path

HOME = Path.home()
WORK_DB = HOME / ".aos" / "data" / "work.db"
BACKUP_DIR = HOME / ".aos" / "backups" / "pre-purge"

# Kept in step with core/engine/work/backend.py AUTO_THREAD_PREFIX.
_MATCH = """
    status = 'exploring'
    AND title LIKE 'Work in %'
    AND project_id IS NULL
"""


def _connect(readonly: bool = False) -> sqlite3.Connection | None:
    if not WORK_DB.exists():
        return None
    try:
        if readonly:
            return sqlite3.connect(f"file:{WORK_DB}?mode=ro", uri=True)
        return sqlite3.connect(str(WORK_DB))
    except sqlite3.Error:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _has_project_column(conn: sqlite3.Connection) -> bool:
    return "project_id" in {r[1] for r in conn.execute("PRAGMA table_info(threads)")}


def _matching(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM threads WHERE {_MATCH}").fetchone()[0]


def check() -> bool:
    conn = _connect(readonly=True)
    if conn is None:
        return True  # no work.db on this machine
    try:
        if not _table_exists(conn, "threads") or not _has_project_column(conn):
            return True
        return _matching(conn) == 0
    except sqlite3.Error:
        return True
    finally:
        conn.close()


def up() -> bool:
    conn = _connect(readonly=True)
    if conn is None:
        print("  No work.db on this machine — nothing to close")
        return True
    try:
        if not _table_exists(conn, "threads"):
            print("  work.db has no threads table — nothing to close")
            return True
        if not _has_project_column(conn):
            print("  threads has no project_id column — nothing to key on")
            return True
        matched = _matching(conn)
        total = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        still_open = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'exploring'"
        ).fetchone()[0] - matched
        top = conn.execute(
            f"SELECT title, COUNT(*) c FROM threads WHERE {_MATCH} "
            "GROUP BY title ORDER BY c DESC LIMIT 5"
        ).fetchall()
    finally:
        conn.close()

    if matched == 0:
        print("  No cwd-keyed auto threads left to close")
        return True

    print(f"  {matched} of {total} thread(s) match; {still_open} exploring thread(s) stay open")
    for title, count in top:
        print(f"    {count:>5}  {title!r}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"work.db.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(WORK_DB, dest)
        print(f"  ✓ Backed up work.db → {dest}")
    except OSError as e:
        print(f"  ✗ Could not back up work.db ({e}) — refusing to update")
        return False

    conn = _connect()
    if conn is None:
        return False
    try:
        cur = conn.execute(f"UPDATE threads SET status = 'closed' WHERE {_MATCH}")
        closed = cur.rowcount or 0
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        print(f"  ✗ Close failed ({e}) — rolled back; backup at {dest}")
        return False
    finally:
        conn.close()

    print(f"  ✓ Closed {closed} cwd-keyed thread(s)")
    print("     Reversible: UPDATE threads SET status='exploring' WHERE status='closed'")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 125 already applied" if check() else ("Done" if up() else "Failed"))
