-- Work engine test schema — isolated DB fixture for tests/test_engine.py.
--
-- Captured from the live migration-patched work.db schema (the accumulation of
-- core/infra/migrations/*), because there is no single canonical schema file:
-- schemas/qareen.sql is documented to drift from the live schema (see
-- migration 050). This DDL is data — regenerate it when the work-table schema
-- changes with:
--   sqlite3 ~/.aos/data/work.db \
--     "SELECT sql || ';' FROM sqlite_master WHERE sql IS NOT NULL \
--      AND name NOT LIKE 'tasks_fts_%' AND name NOT LIKE 'sqlite_%' ORDER BY rowid;" \
--     > tests/fixtures/work_schema.sql
-- (FTS5 shadow tables are excluded — CREATE VIRTUAL TABLE tasks_fts recreates them.)

CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    path            TEXT,
    goal            TEXT,
    done_when       TEXT,
    telegram_bot_key    TEXT,
    telegram_chat_key   TEXT,
    telegram_forum_topic INTEGER,
    stages          TEXT,
    current_stage   TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT
);
CREATE TABLE areas (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    standard        TEXT,              -- what "healthy" looks like
    review_cadence  TEXT DEFAULT 'weekly',
    parent_id       TEXT REFERENCES areas(id),
    is_active       INTEGER DEFAULT 1,
    metrics         TEXT               -- JSON array of KPI definitions
);
CREATE TABLE goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    weight          INTEGER DEFAULT 0,
    description     TEXT,
    project_id      TEXT REFERENCES projects(id)
);
CREATE TABLE key_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals(id),
    title           TEXT NOT NULL,
    progress        INTEGER DEFAULT 0,
    target          TEXT
);
CREATE TABLE workflows (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    trigger_type    TEXT DEFAULT 'manual',  -- manual, scheduled, event
    trigger_config  TEXT,                   -- JSON (cron string, event type)
    task_templates  TEXT NOT NULL,          -- JSON array of task templates
    project_template TEXT,                  -- JSON optional project template
    assignee_defaults TEXT,                 -- JSON map of role → assignee
    is_active       INTEGER DEFAULT 1,
    run_count       INTEGER DEFAULT 0,
    last_run_at     TEXT
);
CREATE TABLE workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
    status          TEXT DEFAULT 'running',  -- running, completed, failed, cancelled
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    project_id      TEXT REFERENCES projects(id),
    task_ids        TEXT,                    -- JSON array of created task IDs
    triggered_by    TEXT DEFAULT 'operator', -- operator, agent, schedule, event
    trigger_event   TEXT                     -- JSON event data
);
CREATE TABLE threads (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'active',
    created_at      TEXT,
    project_id      TEXT REFERENCES projects(id)
);
CREATE TABLE inbox (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    project_id      TEXT REFERENCES projects(id)
);
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'todo',
    priority        INTEGER NOT NULL DEFAULT 3,
    project_id      TEXT REFERENCES projects(id),
    description     TEXT,
    assigned_to     TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    due_at          TEXT,
    parent_id       TEXT REFERENCES tasks(id),
    pipeline        TEXT,
    pipeline_stage  TEXT,
    recurrence      TEXT,
    tags            TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT
, scheduled_at TEXT, snoozed_until TEXT, estimate_minutes INTEGER, story_points REAL, actual_minutes INTEGER, energy TEXT, context TEXT, area_id TEXT, assignee_type TEXT DEFAULT 'operator', recurrence_type TEXT DEFAULT 'fixed', template_id TEXT, recurrence_index INTEGER);
CREATE TABLE task_handoffs (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    state           TEXT NOT NULL,
    next_step       TEXT NOT NULL,
    files           TEXT,
    decisions       TEXT,
    blockers        TEXT,
    session_id      TEXT,
    timestamp       TEXT
);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX idx_tasks_due ON tasks(due_at);
CREATE INDEX idx_tasks_created ON tasks(created_at);
CREATE INDEX idx_areas_active ON areas(is_active);
CREATE INDEX idx_areas_parent ON areas(parent_id);
CREATE INDEX idx_workflows_active ON workflows(is_active);
CREATE INDEX idx_workflow_runs_workflow ON workflow_runs(workflow_id);
CREATE INDEX idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX idx_tasks_status_priority ON tasks(status, priority);
CREATE INDEX idx_tasks_scheduled ON tasks(scheduled_at) WHERE scheduled_at IS NOT NULL;
CREATE INDEX idx_tasks_parent ON tasks(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_tasks_template ON tasks(template_id) WHERE template_id IS NOT NULL;
CREATE VIRTUAL TABLE tasks_fts USING fts5(    title, description, content=tasks, content_rowid=rowid);
