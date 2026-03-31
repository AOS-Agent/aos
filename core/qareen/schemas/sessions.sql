-- sessions.sql — Session history (sessions.db)
-- Qareen session tracking: agent sessions, linked tasks, and decisions.

CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT,
    operator_id         TEXT,
    status              TEXT NOT NULL,       -- active/paused/ended
    started             TEXT NOT NULL,       -- ISO8601
    ended               TEXT,               -- ISO8601
    project             TEXT,
    thread_id           TEXT,
    outcome             TEXT,
    transcript_summary  TEXT,
    utterance_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_tasks (
    session_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    relation    TEXT NOT NULL,               -- completed/created/worked_on
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS session_decisions (
    session_id   TEXT NOT NULL,
    decision_id  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_started   ON sessions(started);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_id  ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status    ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_project   ON sessions(project);
