"""
Migration 084: Freeze the `message_entities` schema for the enrichment engine.

Ambient Knowledge Phase 4. The Phase 2 sample batch (research/entity-extraction-
sample-2026-07-19.md §4) froze a typed, provenance-bearing entity schema. This
migration installs it into comms.db and adds the extraction watermark the engine
resumes from.

THE COLLISION. comms.db already carries a `message_entities` table — but the OLD
one, from a retired enrichment path (INTEGER autoincrement id, message_id,
entity_type, entity_id, confidence; ~1900 orphan `topic` rows). No live code on
main reads or writes it (only docs mention it). The frozen schema reuses that
exact name with a completely different shape, so this migration RENAMES the old
table to `message_entities_legacy` — preserving every row (component-lifecycle
rule: never hard-delete instance data) — and creates the frozen table in its
place. The legacy rows stay queryable; nothing points at them.

IDEMPOTENT BY SCHEMA INSPECTION, not a schema-exists skip. `up()` discriminates
the OLD shape (has `entity_id`, lacks `fields_json`) from the NEW shape (has
`fields_json`) via PRAGMA table_info, so a re-run never re-renames the frozen
table into legacy and never clobbers data. There is deliberately no `check()`
short-circuit: the runner's version watermark gates re-execution, and a
schema-exists `check()` is exactly the false-"already applied" guard the
migration-class lessons warn against. `up()` is safe to run repeatedly.
"""

DESCRIPTION = "Freeze message_entities schema + extraction watermark (Phase 4)"

import sqlite3
from pathlib import Path

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Frozen schema — research/entity-extraction-sample-2026-07-19.md §4.
_FROZEN_SQL = """
CREATE TABLE IF NOT EXISTS message_entities (
    id                TEXT PRIMARY KEY,     -- ent_<hash> (stable per version)
    entity_type       TEXT NOT NULL,        -- topic|commitment|transaction|
                                            --   event|mention|question_open
    value             TEXT,                 -- denormalized primary string
    fields_json       TEXT NOT NULL,        -- typed fields per entity_type
    confidence        REAL NOT NULL,        -- 0-1, model-calibrated
    source_ids        TEXT NOT NULL,        -- JSON array of messages.id
    person_id         TEXT,                 -- resolved subject (scope)
    channel           TEXT,
    batch_key         TEXT NOT NULL,        -- person-day batch of origin
    extractor_version TEXT NOT NULL,        -- e.g. "extract@1"
    model             TEXT NOT NULL,        -- "claude-haiku-4-5-20251001"
    created_at        TEXT NOT NULL,
    ontology_type     TEXT,                 -- "transaction"|"reminder"|null
    ontology_id       TEXT,                 -- object id once lifted
    status            TEXT NOT NULL DEFAULT 'active'  -- active|superseded|dismissed
);
CREATE INDEX IF NOT EXISTS idx_me_person  ON message_entities(person_id);
CREATE INDEX IF NOT EXISTS idx_me_type    ON message_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_me_conf    ON message_entities(confidence);
CREATE INDEX IF NOT EXISTS idx_me_version ON message_entities(extractor_version);
CREATE INDEX IF NOT EXISTS idx_me_status  ON message_entities(status);
CREATE INDEX IF NOT EXISTS idx_me_batch   ON message_entities(batch_key);

-- FTS over `value` for recall. External-content table keyed on the implicit
-- rowid (the table keeps a rowid — it is not WITHOUT ROWID).
CREATE VIRTUAL TABLE IF NOT EXISTS message_entities_fts USING fts5(
    value, content='message_entities', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS message_entities_fts_insert
AFTER INSERT ON message_entities BEGIN
    INSERT INTO message_entities_fts(rowid, value) VALUES (new.rowid, new.value);
END;
CREATE TRIGGER IF NOT EXISTS message_entities_fts_delete
AFTER DELETE ON message_entities BEGIN
    INSERT INTO message_entities_fts(message_entities_fts, rowid, value)
        VALUES('delete', old.rowid, old.value);
END;
CREATE TRIGGER IF NOT EXISTS message_entities_fts_update
AFTER UPDATE ON message_entities BEGIN
    INSERT INTO message_entities_fts(message_entities_fts, rowid, value)
        VALUES('delete', old.rowid, old.value);
    INSERT INTO message_entities_fts(rowid, value) VALUES (new.rowid, new.value);
END;

-- Extraction watermark. Idempotent resume per extractor_version. `status`
-- distinguishes a real extraction from a spam-skip so skipped messages are
-- never re-attempted (a spam-skip is a terminal decision for that version).
CREATE TABLE IF NOT EXISTS message_extraction (
    message_id        TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    extracted_at      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'extracted',  -- extracted|skipped_spam
    PRIMARY KEY (message_id, extractor_version)
);
CREATE INDEX IF NOT EXISTS idx_mx_version ON message_extraction(extractor_version);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def up() -> None:
    if not COMMS_DB.exists():
        # Fresh install with no comms.db yet — the schema lands when the DB is
        # first created by the comms store. Nothing to migrate; not a failure.
        print(f"  comms.db not present at {COMMS_DB} — skipping (fresh install)")
        return

    conn = sqlite3.connect(COMMS_DB)
    try:
        cols = _columns(conn, "message_entities") if _table_exists(conn, "message_entities") else set()

        if cols and "fields_json" not in cols:
            # OLD orphan shape present. Preserve it under a legacy name, unless a
            # prior run already did (then the current table is either gone or new).
            if _table_exists(conn, "message_entities_legacy"):
                # A legacy table already exists AND the live one is still old-shape:
                # a partial prior run. Keep the older legacy, drop the redundant
                # old live table so the frozen CREATE can proceed. Never touches
                # the preserved legacy rows.
                conn.execute("DROP TABLE message_entities")
                print("  message_entities_legacy already present; dropped redundant old table")
            else:
                conn.execute("ALTER TABLE message_entities RENAME TO message_entities_legacy")
                n = conn.execute("SELECT COUNT(*) FROM message_entities_legacy").fetchone()[0]
                print(f"  Renamed old message_entities → message_entities_legacy ({n} rows preserved)")

        conn.executescript(_FROZEN_SQL)
        conn.commit()

        # Verify the frozen shape actually landed — evidence, not assumption.
        frozen = _columns(conn, "message_entities")
        required = {"id", "fields_json", "source_ids", "batch_key",
                    "extractor_version", "status", "ontology_type"}
        missing = required - frozen
        if missing:
            raise RuntimeError(f"frozen message_entities missing columns: {sorted(missing)}")
        if not _table_exists(conn, "message_extraction"):
            raise RuntimeError("message_extraction watermark table not created")
        print("  Frozen message_entities + message_entities_fts + message_extraction ready")
    finally:
        conn.close()


def check() -> bool:
    """True only when the frozen end-state is fully present — a PRECISE state
    check, not a table-exists guard. Returns False if comms.db is absent (let
    up() run its graceful fresh-install skip), if message_entities is still the
    OLD shape (no `fields_json` → not migrated), or if the watermark table is
    missing. up() stays fully idempotent regardless; this only lets the runner
    skip re-running an already-complete migration."""
    if not COMMS_DB.exists():
        return False
    conn = sqlite3.connect(COMMS_DB)
    try:
        if not _table_exists(conn, "message_entities"):
            return False
        if "fields_json" not in _columns(conn, "message_entities"):
            return False
        return _table_exists(conn, "message_extraction")
    finally:
        conn.close()
