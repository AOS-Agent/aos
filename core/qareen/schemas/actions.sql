-- actions.sql — Audit trail (actions.db)
-- Qareen action logging: full audit trail, trust levels, and trust history.

CREATE TABLE IF NOT EXISTS audit_log (
    id           TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,              -- ISO8601
    actor        TEXT NOT NULL,
    actor_type   TEXT NOT NULL,              -- operator/agent
    action_name  TEXT NOT NULL,
    params       TEXT,                       -- JSON
    success      INTEGER NOT NULL,           -- 0/1
    result       TEXT,                       -- JSON
    error        TEXT,
    duration_ms  INTEGER
);

CREATE TABLE IF NOT EXISTS trust_entries (
    agent_id             TEXT NOT NULL,
    action_type          TEXT NOT NULL,
    trust_level          INTEGER NOT NULL,
    acceptance_rate      REAL NOT NULL DEFAULT 0,
    total_actions        INTEGER NOT NULL DEFAULT 0,
    last_promoted        TEXT,              -- ISO8601
    last_demoted         TEXT,              -- ISO8601
    circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, action_type)
);

CREATE TABLE IF NOT EXISTS trust_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    action_type TEXT NOT NULL,
    old_level   INTEGER NOT NULL,
    new_level   INTEGER NOT NULL,
    reason      TEXT,
    timestamp   TEXT NOT NULL               -- ISO8601
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp    ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor        ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_name  ON audit_log(action_name);
