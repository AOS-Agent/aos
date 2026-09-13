"""
Migration 126: give `inbox` the columns reconcile's dedup needs (aos#239).

Reconcile checks return NOTIFY on essentially every run — dead_code,
storage_layout, vault_contract, instance_hygiene, arms_coverage and
context_freshness all measured at ~1,895 NOTIFYs out of ~1,895 runs
(2026-09-13) — and the only consumer was ~/.aos/logs/reconcile.jsonl (plus,
for a few checks, a Telegram ping that dedupes into silence after the first
occurrence). A finding nothing ever turns into a to-do is not being acted on;
it is being logged at a human.

core/infra/reconcile/inbox_sink.py closes that loop: every NOTIFY becomes one
work-inbox row, deduplicated by (source=`reconcile:<check>`, a fingerprint of
the humanized finding), so a re-firing standing condition updates one row's
`count`/`last_seen` instead of minting a new capture every cycle, and a check
that resolves (OK or auto-FIXED) has its row dropped automatically. That
needs three columns `inbox` does not have on any machine that predates this
release: `fingerprint`, `count`, `last_seen`.

The adapter (`WorkAdapter._ensure_aux_schema`, core/engine/work/ontology/
work.py) already adds these columns at construction time — same as it already
does for `inbox.source`/`inbox.snoozed_until` (and, before that, `threads.cwd`
in migration 121). Fresh installs and the test fixture work without this
migration having run first. This migration is the explicit, auditable
instance-layer bridge the component-lifecycle rule requires for an existing
machine's work.db, which the adapter will otherwise only patch the next time
something happens to open it.

Idempotent: each `ALTER TABLE ... ADD COLUMN` runs only if the column is
absent. Reversible only by recreating the table without the columns (SQLite
has no widely-available DROP COLUMN here) — not offered; all three are
additive and nullable/defaulted, so leaving them in place on rollback is
harmless.
"""

from __future__ import annotations

DESCRIPTION = "Add inbox.fingerprint/count/last_seen for reconcile dedup (aos#239)"

import sqlite3
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _work_db() -> Path:
    return Path.home() / ".aos" / "data" / "work.db"

NEW_COLUMNS = {
    "fingerprint": "ALTER TABLE inbox ADD COLUMN fingerprint TEXT",
    "count": "ALTER TABLE inbox ADD COLUMN count INTEGER DEFAULT 1",
    "last_seen": "ALTER TABLE inbox ADD COLUMN last_seen TEXT",
}


def _connect() -> sqlite3.Connection | None:
    if not _work_db().exists():
        return None
    try:
        return sqlite3.connect(str(_work_db()))
    except sqlite3.Error:
        return None


def _inbox_cols(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}


def check() -> bool:
    conn = _connect()
    if conn is None:
        return True  # no work.db yet — adapter creates it with these columns
    try:
        cols = _inbox_cols(conn)
        if not cols:
            return True  # no inbox table yet either
        return all(name in cols for name in NEW_COLUMNS)
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
        cols = _inbox_cols(conn)
        if not cols:
            print("  work.db has no inbox table — adapter creates it with these columns on first use")
            return True
        added = []
        for name, ddl in NEW_COLUMNS.items():
            if name in cols:
                continue
            conn.execute(ddl)
            added.append(name)
        conn.commit()
        if added:
            print(f"  Added inbox columns: {', '.join(added)}")
        else:
            print("  inbox.fingerprint/count/last_seen already present")
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
    print("Migration 126 already applied" if check() else ("Done" if up() else "Failed"))
