"""Migration 091: one-shot import of the islah ``bugs.yaml`` ledger into work.db.

Kanban Phase 3 (islah data plane). Each of the operator's islah bugs becomes a
``pipeline='bug'`` task with its lifecycle reconstructed as a faithful
``task_activity`` narrative (reported → triaged → attempts → proof → status),
ORIGINAL timestamps preserved. Idempotent: keyed on ``islah:<id>`` activity
markers, so re-running imports nothing new (the same path the mirror cron uses).

Instance-data transform only — reads ``~/.aos/islah/bugs.yaml`` (internal SSD,
operator data, never committed) and writes the operator's ``work.db``. On a
fresh install with neither file, it is a no-op. The importer engine and round-
trip acceptance (qg#1) are covered by tests/engine/work/test_islah_import.py
against a FAKE ledger fixture; this migration runs the live import at deploy.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import yaml

DESCRIPTION = "Kanban Phase 3: one-shot import of islah bugs.yaml → work.db"

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "engine" / "work")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_BUGS_YAML = Path.home() / ".aos" / "islah" / "bugs.yaml"


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


def _bug_ids() -> list[str]:
    if not _BUGS_YAML.exists():
        return []
    try:
        raw = yaml.safe_load(_BUGS_YAML.read_text()) or {}
    except Exception:
        return []
    return [b.get("id") for b in (raw.get("bugs") or []) if b.get("id")]


def check() -> bool:
    """Applied iff every ledger bug already has its ``islah:<id>:created`` beat."""
    ids = _bug_ids()
    if not ids:
        return True  # nothing to import (no ledger / fresh install)
    db = _work_db_path()
    if not db.exists():
        return True
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_activity'"
        ).fetchone():
            return False  # ledger present but nothing imported yet
        pending = 0
        for bid in ids:
            if not conn.execute(
                "SELECT 1 FROM task_activity WHERE source_event_id = ?",
                (f"islah:{bid}:created",),
            ).fetchone():
                pending += 1
        return pending == 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def up() -> bool:
    if not _bug_ids():
        print("       no islah bugs.yaml to import — skipping (fresh install / no ledger)")
        return True
    dbp = _work_db_path()
    os.environ["AOS_WORK_DB"] = str(dbp)
    # Bind the backend singleton onto the resolved work DB. DB_PATH is resolved
    # once at backend import from AOS_WORK_DB; set it explicitly so a backend
    # already imported in-process (tests, chained migrations) still binds here.
    try:
        import backend as _b
        _b.DB_PATH = Path(dbp)
        _b._adapter = None
        _b._resolver = None
        _b._project_ctx = None
    except Exception:
        pass

    from core.engine.work.intake.islah_import import import_bugs

    res = import_bugs(_BUGS_YAML)
    print(f"       Imported islah ledger: {res['created']} created, "
          f"{res['skipped']} already present, {res['activities']} activity rows "
          f"({res['total']} bugs total)")
    return True


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
