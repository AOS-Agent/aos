"""
Migration 121: give `threads` back its `cwd` column (aos#223).

Migration 050 seeded work.db's `threads` table from qareen.db's schema at the
time, which had no `cwd` column — so `find_thread_by_cwd` (core/engine/work/
backend.py) has been a permanent `return None` ever since ("DB has no cwd
column"). Every call site read that as "no thread for this directory yet"
and created one, unconditionally, forever: `get_or_create_thread_for_cwd`
(the SessionEnd hook's thread-continuity step, session_close.py) never had a
"found" branch to take.

`~/aos` is a release symlink whose target directory name changes on every
update (v0.7.1-bdd0739, v0.7.6-dff5c0d, ...), and the auto-generated title is
`f"Work in {Path(cwd).name}"` — so even sessions in the exact same logical
checkout minted a fresh row on every release. The live DB accumulated 4,635
such rows across only 23 distinct titles, all `status='exploring'`, 0 ever
promoted (audit 2026-09-13, aos#223) — this is the same flood migration 115
closes retroactively; 115 is a one-time sweep and intentionally does not
touch the generator. This migration plus the code fix in the same commit
(core/engine/work/ontology/work.py, backend.py) close the loop: 115 mops up
the existing rows, this restores the column, and the generator itself now
finds-or-reuses instead of creating unconditionally. **The release plan's
"generation of Work in … threads is unchanged" decision is reversed as of
this migration — the generator is fixed in this same release**; see
docs/releases/0.7.7.md.

The adapter (`WorkAdapter._ensure_aux_schema`) already adds this column at
construction time — fresh installs and the test fixture work without this
migration having run first, same as migration 087 for the board-honesty
tables. This migration is the explicit, auditable instance-layer bridge the
component-lifecycle rule requires for existing machines' work.db.

Idempotent: `ALTER TABLE ... ADD COLUMN` runs only if the column is absent.
Reversible only by recreating the table without the column (SQLite has no
DROP COLUMN in wide use here) — not offered; the column is additive and
nullable, so leaving it in place on rollback is harmless.
"""

from __future__ import annotations

DESCRIPTION = "Add threads.cwd so get_or_create_thread_for_cwd can dedupe (aos#223)"

import sqlite3
from pathlib import Path

HOME = Path.home()
WORK_DB = HOME / ".aos" / "data" / "work.db"


def _connect() -> sqlite3.Connection | None:
    if not WORK_DB.exists():
        return None
    try:
        return sqlite3.connect(str(WORK_DB))
    except sqlite3.Error:
        return None


def _thread_cols(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(threads)")}


def check() -> bool:
    conn = _connect()
    if conn is None:
        return True  # no work.db yet — adapter creates it with cwd on first use
    try:
        cols = _thread_cols(conn)
        if not cols:
            return True  # no threads table yet either
        return "cwd" in cols
    except sqlite3.Error:
        return True
    finally:
        conn.close()


def up() -> bool:
    conn = _connect()
    if conn is None:
        print("  No work.db on this machine — nothing to migrate")
        return True
    try:
        cols = _thread_cols(conn)
        if not cols:
            print("  work.db has no threads table — adapter creates it with cwd on first use")
            return True
        if "cwd" in cols:
            print("  threads.cwd already present")
            return True
        conn.execute("ALTER TABLE threads ADD COLUMN cwd TEXT")
        conn.commit()
        print("  Added threads.cwd")
        return True
    except sqlite3.Error as e:
        conn.rollback()
        print(f"  ✗ Add column failed ({e})")
        return False
    finally:
        conn.close()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 121 already applied" if check() else ("Done" if up() else "Failed"))
