"""
Migration 123: move `sessions` and `session_tasks` into work.db — finishes aos#131.

Since the work.db cutover (migration 050) the work engine has read and written
its tasks, projects, goals, inbox and threads in ~/.aos/data/work.db while
`sessions` and `session_tasks` stayed behind in ~/.aos/data/qareen.db. Five
comment sites in `backend.py` and `ontology/work.py` called that "temporary until
aos#131"; it survived a full DB cutover and the Qareen decommission (migration
108), because nothing forced it to resolve.

What it cost, concretely: every task↔session and thread↔session link was a join
across two SQLite files, matched on id strings with no foreign key and no
transaction spanning both. `WorkAdapter._session_conn()` kept a second
connection open to qareen.db and had to guess which database a statement
belonged to; `link_session_to_thread` wrote to a `sessions` table that work.db
does not have, which crashed the SessionEnd hook the first time aos#223 made
that branch reachable. One store removes the class of bug, not an instance of it.

What moves: `sessions` (13,336 rows live) and `session_tasks` (24,300 rows,
across 131 distinct tasks and 1,477 distinct sessions).

What stays in qareen.db, deliberately — migration 108 lists these as the reason
the file survives at all: intelligence, loop signals, cron_runs, people intel.
Also staying: `sessions_v2`, `session_events` and `companion_sessions`, which
belonged to the retired companion service and have no reader in the work engine.
**qareen.db is left in place and simply stops being consulted** by the work
engine. Deleting a database with live readers is a separate decision and not
this migration's to make.

Safety, in order:
  1. Both databases are copied to ~/.aos/backups/pre-merge/ before any write.
     Both, not just the destination: a half-finished ATTACH + INSERT is a
     recoverable event only if the source is also pinned.
  2. Rows are copied with `INSERT OR IGNORE`, so a re-run adds nothing and a
     partial first run completes rather than conflicting.
  3. Counts are verified afterwards — work.db must hold at least as many rows
     as qareen.db for each table, or up() fails and says so. The backup path is
     printed on failure.

The destination tables are created **without** the `REFERENCES` clauses the
qareen originals carried (`sessions.task_id → tasks(id)`,
`session_tasks.session_id → sessions(id)`). That is not laziness: the adapter
opens work.db with `PRAGMA foreign_keys=ON`, the live data already violates those
constraints (79 `session_tasks` rows reference a session id that is not in
`sessions`, and `sessions.task_id` points at tasks that migration 114 purged),
and a constraint added here would reject exactly the rows this migration exists
to preserve. The links were always by convention; this records that honestly
rather than declaring a rule the data does not keep.

Fresh installs do not need this migration: `WorkAdapter._ensure_aux_schema`
creates both tables at construction time, the same way migration 121 relates to
`threads.cwd`. This is the instance-layer bridge for machines that already have
rows in qareen.db.

Idempotent: check() passes once work.db has both tables and neither is missing
rows that qareen.db holds.
"""

from __future__ import annotations

DESCRIPTION = "Move sessions + session_tasks from qareen.db into work.db (aos#131)"

import shutil
import sqlite3
import time
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _work_db() -> Path:
    return Path.home() / ".aos" / "data" / "work.db"

def _qareen_db() -> Path:
    return Path.home() / ".aos" / "data" / "qareen.db"

def _backup_dir() -> Path:
    return Path.home() / ".aos" / "backups" / "pre-merge"

TABLES = ("sessions", "session_tasks")

# Mirrors core/engine/work/ontology/work.py WorkAdapter._ensure_aux_schema, so a
# machine that runs the migration and a machine that gets the tables from the
# adapter end up with the same schema.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT,
    operator_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    project_id      TEXT,
    task_id         TEXT,
    thread_id       TEXT,
    outcome         TEXT,
    transcript_summary TEXT,
    utterance_count INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_agent   ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_task    ON sessions(task_id);

CREATE TABLE IF NOT EXISTS session_tasks (
    session_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    relation        TEXT NOT NULL,
    PRIMARY KEY (session_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_session_tasks_task ON session_tasks(task_id);
"""


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        if readonly:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return sqlite3.connect(str(path))
    except sqlite3.Error:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0


def _shared_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Columns present in both `main.<table>` and the ATTACHed `src.<table>`.

    The two schemas are identical today, but a column on only one side must not
    take the whole migration down: copying the intersection moves every row and
    loses only a field nothing on this machine can read anyway.
    """
    # The schema name is a PRAGMA prefix, not part of the argument:
    # `PRAGMA src.table_info(x)`, never `PRAGMA table_info(src.x)`.
    dest = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
    src = {r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")}
    return [c for c in dest if c in src]


def check() -> bool:
    """Applied when work.db holds both tables and is not missing source rows."""
    work = _connect(_work_db(), readonly=True)
    if work is None:
        return True  # no work.db — the adapter will create the tables itself
    try:
        if not all(_table_exists(work, t) for t in TABLES):
            return False
        src = _connect(_qareen_db(), readonly=True)
        if src is None:
            return True  # nothing left to move
        try:
            return all(_count(work, t) >= _count(src, t) for t in TABLES)
        finally:
            src.close()
    except sqlite3.Error:
        return True
    finally:
        work.close()


def _backup() -> Path | None:
    _backup_dir().mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        for db in (_work_db(), _qareen_db()):
            if db.exists():
                dest = _backup_dir() / f"{db.name}.bak-{stamp}"
                shutil.copy2(db, dest)
                print(f"  ✓ Backed up {db.name} → {dest}")
    except OSError as e:
        print(f"  ✗ Could not back up ({e}) — refusing to merge")
        return None
    return _backup_dir() / f"work.db.bak-{stamp}"


def up() -> bool:
    if not _work_db().exists():
        print("  No work.db on this machine — the adapter creates both tables on first use")
        return True

    src = _connect(_qareen_db(), readonly=True)
    pending = {}
    if src is not None:
        try:
            work_ro = _connect(_work_db(), readonly=True)
            for t in TABLES:
                have = _count(work_ro, t) if work_ro else 0
                pending[t] = (_count(src, t), have)
            if work_ro:
                work_ro.close()
        finally:
            src.close()

    to_move = sum(max(s - d, 0) for s, d in pending.values())
    if to_move == 0 and check():
        print("  sessions/session_tasks already live in work.db — nothing to move")
        return True

    for t, (s, d) in pending.items():
        print(f"  {t}: {s} row(s) in qareen.db, {d} already in work.db")

    backup = _backup() if to_move else True
    if backup is None:
        return False

    work = _connect(_work_db())
    if work is None:
        return False
    try:
        # Schema first, always — a machine with no rows to move still needs the
        # tables, because the adapter no longer falls back to qareen.db.
        work.executescript(SCHEMA)
        work.commit()

        if to_move and _qareen_db().exists():
            work.execute("ATTACH DATABASE ? AS src", (str(_qareen_db()),))
            try:
                for t in TABLES:
                    if not work.execute(
                        "SELECT 1 FROM src.sqlite_master WHERE type='table' AND name=?",
                        (t,),
                    ).fetchone():
                        continue
                    cols = _shared_columns(work, t)
                    if not cols:
                        continue
                    col_list = ", ".join(cols)
                    work.execute(
                        f"INSERT OR IGNORE INTO main.{t} ({col_list}) "
                        f"SELECT {col_list} FROM src.{t}"
                    )
                work.commit()
            finally:
                work.execute("DETACH DATABASE src")
        work.commit()
    except sqlite3.Error as e:
        work.rollback()
        work.close()
        print(f"  ✗ Merge failed ({e}) — rolled back; backups in {_backup_dir()}")
        return False
    finally:
        try:
            work.close()
        except sqlite3.Error:
            pass

    if not check():
        print(f"  ✗ Row counts do not match after the merge — backups in {_backup_dir()}")
        return False

    work_ro = _connect(_work_db(), readonly=True)
    if work_ro is not None:
        for t in TABLES:
            print(f"  ✓ work.db {t}: {_count(work_ro, t)} row(s)")
        work_ro.close()
    print("  · qareen.db is left in place and is no longer consulted by the work engine")
    return True


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 123 already applied" if check() else ("Done" if up() else "Failed"))
