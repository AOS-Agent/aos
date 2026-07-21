"""
Migration 090: Intelligence Loop Phase 1 — signals + proposals substrate.

Council decisions locked for Phase 1 (Intelligence Loop spec, council session
2026-07-21):

1. NO NEW SUBSTRATE. Both tables live in qareen.db alongside intelligence_briefs
   (core/engine/intelligence/ingest/store.py) rather than a new database file —
   one fewer thing to back up, migrate, and reconcile.

2. TAINT FROM DAY ONE. `signals.tainted` marks a signal derived from external
   content (comms, web, third-party text) as opposed to first-party system
   state (friction judge, board honesty). `proposals.tainted` is the OR of its
   evidence signals' taint, computed once at proposal-creation time and stored
   (not recomputed on read) — a proposal's taint status must be a stable,
   auditable fact even if evidence rows are later pruned. Tainted proposals are
   surfaced for review, never auto-applied — the enforcement lives in the
   writer library (core/engine/loop/signals.py), not here.

3. LAZY EXPIRY, NO CRON. `proposals.expires_at` is enforced by a WHERE-guarded
   UPDATE any reader can run (signals.lazy_expire()) — not a scheduled job.
   Nothing about the schema itself expires rows; this migration only creates
   the column and index that make lazy expiry possible.

4. APPEND-ONLY SIGNALS. `signals` has no status/update path — every row is a
   permanent observation. `proposals` is the only mutable entity, and only
   through the guarded status transitions in the writer library.

IDEMPOTENT BY SCHEMA INSPECTION, matching migration 084's pattern: `up()` uses
CREATE TABLE IF NOT EXISTS plus a PRAGMA table_info verification pass, not a
`check()` short-circuit. The runner's version watermark gates re-execution;
`up()` itself is safe to run any number of times against any prior state
(including a partially-created table from an interrupted first run — CREATE
TABLE IF NOT EXISTS is enough here because, unlike migration 084, there is no
prior *conflicting* shape to detect and rename out of the way).
"""

DESCRIPTION = "Intelligence Loop Phase 1: signals + proposals tables (qareen.db)"

import sqlite3
from pathlib import Path

QAREEN_DB = Path.home() / ".aos" / "data" / "qareen.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id           TEXT PRIMARY KEY,      -- sig_<12 hex> = sha1(sensor+payload+created_at)[:12]
    sensor       TEXT NOT NULL,         -- friction_judge|comms_entities|initiative_drift|
                                         --   engagement|subscription:<source>
    signal_type  TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_refs  TEXT NOT NULL,         -- JSON array, provenance, never empty
    tainted      INTEGER NOT NULL DEFAULT 0,  -- 1 = derived from external content
    project_key  TEXT,
    created_at   TEXT NOT NULL          -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_signals_sensor      ON signals(sensor);
CREATE INDEX IF NOT EXISTS idx_signals_type        ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_project_key ON signals(project_key);
CREATE INDEX IF NOT EXISTS idx_signals_created_at  ON signals(created_at);

CREATE TABLE IF NOT EXISTS proposals (
    id            TEXT PRIMARY KEY,     -- prop_<12 hex> = sha1(title+body+created_at)[:12]
    title         TEXT NOT NULL,
    diff_type     TEXT NOT NULL,        -- skill|claude_md|config|agent|process|other
    body          TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,        -- JSON array of signals.id, never empty
    tainted       INTEGER NOT NULL,     -- 1 if ANY evidence signal is tainted (computed at insert)
    project_key   TEXT,
    status        TEXT NOT NULL DEFAULT 'proposed',
                                         -- proposed|surfaced|approved|rejected|lapsed|applied
    created_at    TEXT NOT NULL,        -- ISO-8601 UTC
    expires_at    TEXT NOT NULL,        -- ISO-8601 UTC; enforced lazily, see signals.lazy_expire()
    decided_at    TEXT,                 -- set when status becomes approved|rejected
    outcome_note  TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status      ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_project_key ON proposals(project_key);
CREATE INDEX IF NOT EXISTS idx_proposals_expires_at  ON proposals(expires_at);
"""

_REQUIRED_SIGNALS_COLS = {
    "id", "sensor", "signal_type", "payload_json", "source_refs",
    "tainted", "project_key", "created_at",
}
_REQUIRED_PROPOSALS_COLS = {
    "id", "title", "diff_type", "body", "evidence_refs", "tainted",
    "project_key", "status", "created_at", "expires_at", "decided_at",
    "outcome_note",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def up() -> None:
    # qareen.db is created by the intelligence store / work adapter on first
    # use elsewhere in the system. A fresh install with no qareen.db yet is
    # not a failure — the tables land the first time anything touches the DB
    # after this migration's version watermark is recorded, same posture as
    # migration 084's comms.db fresh-install skip.
    QAREEN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(QAREEN_DB)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        # Verify the shape actually landed — evidence, not assumption.
        sig_cols = _columns(conn, "signals")
        missing_sig = _REQUIRED_SIGNALS_COLS - sig_cols
        if missing_sig:
            raise RuntimeError(f"signals missing columns: {sorted(missing_sig)}")

        prop_cols = _columns(conn, "proposals")
        missing_prop = _REQUIRED_PROPOSALS_COLS - prop_cols
        if missing_prop:
            raise RuntimeError(f"proposals missing columns: {sorted(missing_prop)}")

        print(f"  signals + proposals ready on {QAREEN_DB}")
    finally:
        conn.close()


def check() -> bool:
    """True only when both tables are fully present with the expected shape.

    Mirrors 084's precise-state check (not a bare table-exists guard) so the
    runner can skip an already-complete migration. up() stays fully
    idempotent regardless of what check() returns.
    """
    if not QAREEN_DB.exists():
        return False
    conn = sqlite3.connect(QAREEN_DB)
    try:
        if not _table_exists(conn, "signals") or not _table_exists(conn, "proposals"):
            return False
        if _REQUIRED_SIGNALS_COLS - _columns(conn, "signals"):
            return False
        if _REQUIRED_PROPOSALS_COLS - _columns(conn, "proposals"):
            return False
        return True
    finally:
        conn.close()
