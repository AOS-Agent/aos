"""
Migration 093: Auto Tracker storage layer — shipment tracking tables in qareen.db.

Creates the full Auto Tracker schema (see core/qareen/schemas/qareen.sql,
"AUTO TRACKER" section): shipments, shipment_events (append-only),
shipment_numbers (international handoffs), orders + order_items +
order_shipments (N:M), detection_priors, domain_rules,
shipment_candidates (approval queue), detection_eval, and tracking_state
(key-value: watermarks, singleton lock, quota-exhausted-until, token buckets).

The DDL is imported from qareen.tracking.store.SCHEMA_SQL — the runtime
store self-initializes from the same string (house pattern: the feature
works even before migrations run), so this migration and the store can
never drift. All statements are CREATE TABLE/INDEX IF NOT EXISTS:
idempotent by construction, safe to re-run, and additive-only (no
existing qareen.db tables are touched).

`up(db_path=...)` / `check(db_path=...)` accept an optional path override
so tests can apply the migration to a COPY of the real DB; the runner
calls them with no arguments and gets ~/.aos/data/qareen.db.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional, Union

DESCRIPTION = "Auto Tracker: shipment tracking tables in qareen.db"

# Migration files are loaded by path (runner.py), not as part of the core
# package, so put the import root on sys.path before importing the store.
CORE_DIR = Path(__file__).resolve().parents[2]
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from qareen.tracking.store import AUTO_TRACKER_TABLES, SCHEMA_SQL  # noqa: E402

QAREEN_DB = Path.home() / ".aos" / "data" / "qareen.db"


def _resolve(db_path: Optional[Union[str, Path]]) -> Path:
    return Path(db_path) if db_path else QAREEN_DB


def _existing_tables(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def up(db_path: Optional[Union[str, Path]] = None) -> bool:
    """Apply the Auto Tracker DDL to qareen.db. Idempotent.

    Creates the DB file when absent (fresh machine — qareen.db is
    otherwise seeded by the qareen service); the store's runtime
    self-init covers the same case.
    """
    path = _resolve(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        # Verify, don't assume: every table must actually be present.
        missing = [t for t in AUTO_TRACKER_TABLES
                   if t not in _existing_tables(conn)]
        if missing:
            print(f"       ERROR: tables missing after apply: {missing}")
            return False
        print(f"       Auto Tracker tables ready in {path} "
              f"({len(AUTO_TRACKER_TABLES)} tables)")
        return True
    except Exception as e:
        print(f"       ERROR applying Auto Tracker schema: {e}")
        return False
    finally:
        conn.close()


def check(db_path: Optional[Union[str, Path]] = None) -> bool:
    """True only when every Auto Tracker table exists. A precise end-state
    check, not a marker-file guard."""
    path = _resolve(db_path)
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        existing = _existing_tables(conn)
        return all(t in existing for t in AUTO_TRACKER_TABLES)
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 093 already applied")
    else:
        print("Done" if up() else "Failed")
