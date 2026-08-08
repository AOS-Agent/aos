-- Converse — Conversation-Session engine schema.
-- Lives inside the existing ~/.aos/data/comms.db (no new database — see
-- PLAN.md §2, ~/.aos/tmp/sessions-build/PLAN.md). Applied by
-- core/infra/migrations/100_converse_init.py and lazily re-applied by
-- converse/db.py's connect() (both idempotent: CREATE TABLE/INDEX IF NOT
-- EXISTS throughout — safe to run repeatedly, safe to run alongside every
-- other comms.db owner).
--
-- Three tables:
--   conversation_sessions — one row per multi-turn conversation (sentinel or
--                            envoy mode) the converse runtime is driving.
--   session_messages      — the transcript. Idempotent ingestion is enforced
--                            by UNIQUE(session_id, channel_message_id): a
--                            duplicate channel poll is a no-op insert, never
--                            a duplicate row.
--   session_actions       — proposed outbound sends / human-touchpoint asks
--                            awaiting operator approval (or already decided).
--
-- Status/state string values are NOT re-declared here as CHECK constraints —
-- the single source of truth for the enums is converse/models.py, imported
-- by every layer (db.py, turn.py, the Qareen API). Keeping the enum out of
-- SQL avoids a migration every time a new status is added.

CREATE TABLE IF NOT EXISTS conversation_sessions (
    id                TEXT PRIMARY KEY,          -- 'cs_' + 12 hex
    mode              TEXT NOT NULL,             -- 'sentinel' | 'envoy'
    voice             TEXT NOT NULL,             -- 'operator' | 'agent'
    channel           TEXT NOT NULL,             -- 'imessage' | 'slack'
    conversation_ref  TEXT NOT NULL,             -- chat.db chat_identifier | Slack channel id (D…)
    counterpart_handle TEXT NOT NULL,            -- +E.164 / email / Slack user id (U…)
    person_id         TEXT,                      -- people.db FK (resolver)
    person_name       TEXT,                      -- denormalized for UI
    mission           TEXT NOT NULL,             -- goal paragraph (context included)
    success_criteria  TEXT,
    constraints        TEXT,
    tools             TEXT NOT NULL DEFAULT 'none',  -- 'none' | 'research' | 'full'
    trust_level       INTEGER NOT NULL DEFAULT 2,    -- 1 gate-all, 2 confidence-gated, 3 autonomous
    status            TEXT NOT NULL,             -- see enum in converse/models.py
    paused_reason     TEXT,                      -- 'operator'|'escalated'|'capped'|'reauth'|'expired'
    cursor            TEXT,                      -- channel ingest cursor (slack ts | chat.db rowid)
    state_summary     TEXT,                      -- handler-maintained compact working state (md, <=2KB)
    artifacts         TEXT,                      -- JSON [{kind:'google_sheet',url,note}, …]
    handling_started_at INTEGER,                 -- crash detection for in-flight handler
    turn_count        INTEGER NOT NULL DEFAULT 0,
    sent_count        INTEGER NOT NULL DEFAULT 0,
    error_count       INTEGER NOT NULL DEFAULT 0,    -- consecutive handler failures
    max_messages      INTEGER NOT NULL DEFAULT 30,
    expires_at        INTEGER,                   -- epoch; NULL = no expiry
    origin            TEXT,                      -- 'trigger:<agent_triggers.id>'|'cli'|'skill'|'qareen'
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    closed_at         INTEGER,
    close_reason      TEXT                       -- 'complete'|'stopped'|'expired'|'capped'|'failed'
);
CREATE INDEX IF NOT EXISTS idx_cs_status  ON conversation_sessions(status);
CREATE INDEX IF NOT EXISTS idx_cs_channel ON conversation_sessions(channel);

CREATE TABLE IF NOT EXISTS session_messages (
    id                 TEXT PRIMARY KEY,         -- 'sm_' + 12 hex
    session_id         TEXT NOT NULL REFERENCES conversation_sessions(id),
    channel_message_id TEXT,                     -- slack ts | chat.db guid (idempotency key)
    role               TEXT NOT NULL,            -- 'contact'|'agent'|'operator'|'system'
    direction          TEXT NOT NULL,            -- 'inbound'|'outbound'|'internal'
    text               TEXT NOT NULL,
    state              TEXT NOT NULL,            -- inbound: received|handling|handled|failed
                                                  -- outbound: queued|sent|send_failed
                                                  -- internal: done
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    error              TEXT,
    ts                 TEXT NOT NULL,            -- ISO8601 (channel time)
    created_at         INTEGER NOT NULL,
    UNIQUE(session_id, channel_message_id)
);
CREATE INDEX IF NOT EXISTS idx_sm_session ON session_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sm_state   ON session_messages(state);

CREATE TABLE IF NOT EXISTS session_actions (
    id           TEXT PRIMARY KEY,               -- 'sa_' + 12 hex
    session_id   TEXT NOT NULL REFERENCES conversation_sessions(id),
    kind         TEXT NOT NULL,                  -- 'send_reply'|'human_touchpoint'|'close'
    payload      TEXT NOT NULL,                  -- JSON: {text} | {description, artifact_url} | {reason}
    gate_reasons TEXT,                            -- JSON list from ConfidenceGate (why it was held)
    status       TEXT NOT NULL,                  -- 'proposed'|'approved'|'rejected'|'executed'|'expired'
    created_at   INTEGER NOT NULL,
    decided_at   INTEGER,
    executed_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sa_pending ON session_actions(status, created_at);
