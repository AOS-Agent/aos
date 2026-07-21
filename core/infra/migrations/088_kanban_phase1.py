"""
Migration 088: Kanban Phase 1 — typed states, delegation, bug task-class.

Layers the Phase 1 model onto the Phase 0 board (migration 087):

  * tasks.delegate    — agent id a task is delegated to (nullable). The future
                        runner polls this; delegation ≠ assignment (spec §3.1).
  * tasks.held_by     — current holder token: 'operator' | 'agent:<name>' | 'none'.
  * tasks.fields      — JSON: bug-class richness (root_cause, code_refs,
                        fix_approach, severity, app, build, screen) without
                        flattening it into fixed columns (dossier §7 risk-1).
  * statuses.pipeline — NULL = generic board column; 'bug' = a bug-pipeline stage.
  * generic 'in_review' status (category 'started') — a real column for anything
    pending human review (islah's awaiting-approval lands here).
  * the 13 bug-pipeline stages seeded as statuses rows (pipeline='bug'), mapped
    onto the six-value category enum per core/engine/work/pipelines.py.

The WorkAdapter also ensures this at construction (fresh installs / tests); this
migration is the explicit instance-layer bridge (atomic-migration rule). Fully
idempotent — column-guarded ALTERs and INSERT OR IGNORE seeds.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

DESCRIPTION = "Kanban Phase 1: delegate/held_by/fields cols, statuses.pipeline, bug stages"

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.work.pipelines import (  # noqa: E402
    BUG_PIPELINE_ID,
    BUG_STAGES,
    GENERIC_STATUSES,
)


def _work_db_path() -> Path:
    env = os.environ.get("AOS_WORK_DB")
    if env:
        return Path(env).expanduser()
    work_db = Path.home() / ".aos" / "data" / "work.db"
    try:
        if work_db.exists() and work_db.stat().st_size > 0:
            c = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True)
            try:
                if c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone():
                    return work_db
            finally:
                c.close()
    except sqlite3.Error:
        pass
    return Path.home() / ".aos" / "data" / "qareen.db"


def _cols(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def check() -> bool:
    db = _work_db_path()
    if not db.exists():
        return True  # adapter ensures on first use
    conn = sqlite3.connect(str(db))
    try:
        if not _has_table(conn, "tasks") or not _has_table(conn, "statuses"):
            return True  # nothing to retrofit yet
        task_ok = {"delegate", "held_by", "fields"} <= _cols(conn, "tasks")
        status_ok = "pipeline" in _cols(conn, "statuses")
        seeded = conn.execute(
            "SELECT count(*) FROM statuses WHERE pipeline = ?", (BUG_PIPELINE_ID,)
        ).fetchone()[0] >= len(BUG_STAGES)
        return task_ok and status_ok and seeded
    finally:
        conn.close()


def up() -> bool:
    db = _work_db_path()
    if not db.exists():
        print("       work DB not present yet — adapter will ensure Phase 1 schema on first use")
        return True
    conn = sqlite3.connect(str(db))
    try:
        if not _has_table(conn, "tasks"):
            print("       no tasks table — nothing to retrofit")
            return True

        tcols = _cols(conn, "tasks")
        if "delegate" not in tcols:
            conn.execute("ALTER TABLE tasks ADD COLUMN delegate TEXT")
        if "held_by" not in tcols:
            conn.execute("ALTER TABLE tasks ADD COLUMN held_by TEXT DEFAULT 'operator'")
        if "fields" not in tcols:
            conn.execute("ALTER TABLE tasks ADD COLUMN fields TEXT")

        if _has_table(conn, "statuses"):
            if "pipeline" not in _cols(conn, "statuses"):
                conn.execute("ALTER TABLE statuses ADD COLUMN pipeline TEXT")
            # Generic statuses (adds in_review; no-op for the Phase 0 seven).
            conn.executemany(
                "INSERT OR IGNORE INTO statuses "
                "(id, name, category, color, position, is_default) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                GENERIC_STATUSES,
            )
            # Bug-pipeline stages.
            conn.executemany(
                "INSERT OR IGNORE INTO statuses "
                "(id, name, category, color, position, is_default, pipeline) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                [
                    (f"bug:{sid}", label, category, color, position, BUG_PIPELINE_ID)
                    for (sid, label, category, _coarse, color, position) in BUG_STAGES
                ],
            )
        conn.commit()
        print(f"       Ensured Kanban Phase 1 schema on {db.name}")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
