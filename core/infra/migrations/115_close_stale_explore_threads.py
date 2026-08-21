"""
Migration 115: close the auto-generated "Work in …" exploration threads.

The `threads` table holds 4,061 rows against 2,124 tasks — nearly 2× the task
count — of which 4,059 are still `exploring` and exactly 2 were ever promoted.
Sampling the titles explains it: they are almost all `"Work in <worktree>"`,
minted one per branch/worktree checkout, several per day, and never closed by
anything. This is session-log noise wearing the shape of curated exploration
(work-engine audit 2026-08-20 §4).

Closes, never deletes. `status='closed'` is reversible with one UPDATE and
keeps the history; a DELETE would throw away the only record of which
worktrees existed when. The thread-*generation* code is deliberately left
alone — changing what the system records is a behaviour change, and this is a
freeze release.

**Window: 7 days, not the 30 the release plan sketched.** 30 days was written
before anyone looked at the age distribution: it matches 15 of 4,057 rows,
because the noise is generated continuously and is nearly all recent (233 rows
on a single day in early August). Seven days closes 3,574 and still leaves
anything from the current working week untouched, including the thread this
session is running under. A rule that de-clutters nothing is not a safer rule,
it is a rule that does not work.

Three conditions, all required:
  1. `status = 'exploring'` — a promoted or already-closed thread is untouched;
  2. `title LIKE 'Work in %'` — the auto-generated shape only. The two
     hand-written threads ("People DB intelligence gaps", "Qren cutover
     night…") do not match and survive;
  3. older than 7 days.

Idempotent: check() passes once nothing matches. Reversible:
`UPDATE threads SET status='exploring' WHERE status='closed'`.
"""

from __future__ import annotations

DESCRIPTION = "Close auto-generated 'Work in …' exploring threads older than 7 days"

import shutil
import sqlite3
import time
from pathlib import Path

HOME = Path.home()
WORK_DB = HOME / ".aos" / "data" / "work.db"
BACKUP_DIR = HOME / ".aos" / "backups" / "pre-purge"

STALE_DAYS = 7

# substr(created_at,1,10) because the column holds both bare dates
# ("2026-08-20") and full ISO timestamps ("2026-03-21T00:00:00"); comparing the
# raw column against date('now', …) silently mismatches the ISO rows.
_MATCH = """
    status = 'exploring'
    AND title LIKE 'Work in %'
    AND substr(created_at, 1, 10) < date('now', ?)
"""
_PARAMS = (f"-{STALE_DAYS} days",)


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


def _matching(conn: sqlite3.Connection) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM threads WHERE {_MATCH}", _PARAMS
    ).fetchone()[0]


def check() -> bool:
    conn = _connect(readonly=True)
    if conn is None:
        return True
    try:
        if not _table_exists(conn, "threads"):
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
        matched = _matching(conn)
        total = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        kept = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'exploring'"
        ).fetchone()[0] - matched
    finally:
        conn.close()

    if matched == 0:
        print("  No stale auto-generated threads to close")
        return True

    print(f"  {matched} of {total} thread(s) match; {kept} exploring thread(s) stay open")

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
        cur = conn.execute(
            f"UPDATE threads SET status = 'closed' WHERE {_MATCH}", _PARAMS
        )
        closed = cur.rowcount or 0
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        print(f"  ✗ Close failed ({e}) — rolled back; backup at {dest}")
        return False
    finally:
        conn.close()

    print(f"  ✓ Closed {closed} auto-generated thread(s) (reversible: set status back to 'exploring')")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 115 already applied" if check() else ("Done" if up() else "Failed"))
