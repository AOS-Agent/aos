-- qareen.sql — The canonical Qareen database (qareen.db)
-- Single database for all Qareen-managed structured data.
-- Foreign keys work across all tables. SQLite WAL mode for concurrency.
--
-- Separate stores: people.db (existing), comms.db (messages), vault/ (markdown)
-- This database holds everything else.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- WORK (migrated from work.yaml)
-- ============================================================

CREATE TABLE IF NOT EXISTS tasks (
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
);

CREATE TABLE IF NOT EXISTS task_handoffs (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    state           TEXT NOT NULL,
    next_step       TEXT NOT NULL,
    files           TEXT,
    decisions       TEXT,
    blockers        TEXT,
    session_id      TEXT,
    timestamp       TEXT
);

CREATE TABLE IF NOT EXISTS projects (
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

CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    weight          INTEGER DEFAULT 0,
    description     TEXT,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS key_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals(id),
    title           TEXT NOT NULL,
    progress        INTEGER DEFAULT 0,
    target          TEXT
);

CREATE TABLE IF NOT EXISTS inbox (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS threads (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'active',
    created_at      TEXT,
    project_id      TEXT REFERENCES projects(id)
);

-- Areas of responsibility (permanent domains)
CREATE TABLE IF NOT EXISTS areas (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    standard        TEXT,              -- what "healthy" looks like
    review_cadence  TEXT DEFAULT 'weekly',
    parent_id       TEXT REFERENCES areas(id),
    is_active       INTEGER DEFAULT 1,
    metrics         TEXT               -- JSON array of KPI definitions
);

-- Workflows (reusable templates that generate tasks)
CREATE TABLE IF NOT EXISTS workflows (
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

-- Workflow runs (executions of workflows)
CREATE TABLE IF NOT EXISTS workflow_runs (
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

-- NOTE: tasks table needs area_id TEXT REFERENCES areas(id) column.
-- Add via migration: ALTER TABLE tasks ADD COLUMN area_id TEXT REFERENCES areas(id);

-- ============================================================
-- SESSIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT,
    operator_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    project_id      TEXT REFERENCES projects(id),
    task_id         TEXT REFERENCES tasks(id),
    thread_id       TEXT,
    outcome         TEXT,
    transcript_summary TEXT,
    utterance_count INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_tasks (
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    task_id         TEXT NOT NULL,
    relation        TEXT NOT NULL,
    PRIMARY KEY (session_id, task_id)
);

-- ============================================================
-- GOVERNANCE
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    actor           TEXT NOT NULL,
    actor_type      TEXT NOT NULL,
    action_name     TEXT NOT NULL,
    params          TEXT,
    context         TEXT,
    success         INTEGER NOT NULL,
    result          TEXT,
    error           TEXT,
    duration_ms     INTEGER,
    task_id         TEXT,
    session_id      TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        REAL
);

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    action_params   TEXT NOT NULL,
    reasoning       TEXT,
    risk_level      TEXT DEFAULT 'medium',
    status          TEXT DEFAULT 'pending',
    decided_by      TEXT,
    decided_at      TEXT,
    expires_at      TEXT,
    task_id         TEXT REFERENCES tasks(id),
    source_utterance TEXT,
    source_card_id  TEXT
);

CREATE TABLE IF NOT EXISTS trust_entries (
    agent_id        TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    trust_level     INTEGER NOT NULL,
    acceptance_rate REAL DEFAULT 0,
    total_actions   INTEGER DEFAULT 0,
    last_promoted   TEXT,
    last_demoted    TEXT,
    circuit_breaker_open INTEGER DEFAULT 0,
    PRIMARY KEY (agent_id, action_type)
);

CREATE TABLE IF NOT EXISTS trust_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    old_level       INTEGER NOT NULL,
    new_level       INTEGER NOT NULL,
    reason          TEXT,
    timestamp       TEXT NOT NULL
);

-- ============================================================
-- ONTOLOGY INFRASTRUCTURE
-- ============================================================

CREATE TABLE IF NOT EXISTS links (
    id              TEXT PRIMARY KEY,
    link_type       TEXT NOT NULL,
    from_type       TEXT NOT NULL,
    from_id         TEXT NOT NULL,
    to_type         TEXT NOT NULL,
    to_id           TEXT NOT NULL,
    direction       TEXT DEFAULT 'directed',
    properties      TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    UNIQUE(link_type, from_type, from_id, to_type, to_id)
);

CREATE TABLE IF NOT EXISTS context_cards (
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    key_facts       TEXT,
    recent_activity TEXT,
    open_items      TEXT,
    built_at        TEXT NOT NULL,
    stale_after     TEXT,
    PRIMARY KEY (entity_type, entity_id)
);

-- Procedures (executable SOPs)
CREATE TABLE IF NOT EXISTS procedures (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    steps           TEXT NOT NULL,          -- JSON ordered array of steps
    owner           TEXT,
    review_interval_days INTEGER DEFAULT 90,
    last_reviewed   TEXT,
    next_review     TEXT,
    linked_workflow TEXT REFERENCES workflows(id),
    project_id      TEXT REFERENCES projects(id),
    tags            TEXT,                   -- JSON array
    version         INTEGER DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT
);

-- ============================================================
-- AGENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_tasks (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    params          TEXT,
    status          TEXT DEFAULT 'queued',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    result          TEXT,
    error           TEXT,
    task_id         TEXT REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value           TEXT NOT NULL,
    learned_at      TEXT NOT NULL,
    source          TEXT,
    confidence      REAL DEFAULT 1.0,
    supersedes_id   INTEGER REFERENCES agent_memory(id),
    UNIQUE(agent_id, key)
);

-- ============================================================
-- PEOPLE EXTENSIONS (pipeline, reminders, transactions)
-- ============================================================

-- Pipeline definitions (sales, hiring, donor cultivation, etc.)
CREATE TABLE IF NOT EXISTS pipeline_definitions (
    name            TEXT PRIMARY KEY,
    description     TEXT,
    stages          TEXT NOT NULL,          -- JSON ordered array of stage names
    default_stage   TEXT,
    closed_won_stages TEXT,                 -- JSON array
    closed_lost_stages TEXT,                -- JSON array
    project_id      TEXT                    -- optional default project
);

-- Pipeline entries (a person's position in a pipeline)
CREATE TABLE IF NOT EXISTS pipeline_entries (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL,          -- FK to people.db (cross-db, string ref)
    pipeline_name   TEXT NOT NULL REFERENCES pipeline_definitions(name),
    stage           TEXT NOT NULL,
    value           REAL DEFAULT 0,
    currency        TEXT DEFAULT 'CAD',
    entered_at      TEXT NOT NULL,
    last_moved_at   TEXT,
    expected_close  TEXT,
    owner           TEXT,                   -- person or agent responsible
    project_id      TEXT REFERENCES projects(id),
    notes           TEXT
);

-- Reminders (follow-up triggers linked to people)
CREATE TABLE IF NOT EXISTS reminders (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL,          -- FK to people.db
    due_date        TEXT NOT NULL,
    note            TEXT,
    recurrence      TEXT,                   -- cron expression
    status          TEXT DEFAULT 'pending', -- pending, done, snoozed, cancelled
    snoozed_until   TEXT,
    created_by      TEXT DEFAULT 'operator',
    task_id         TEXT REFERENCES tasks(id)
);

-- Transactions (financial records linked to people)
CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL,          -- FK to people.db
    amount          REAL NOT NULL,
    currency        TEXT DEFAULT 'CAD',
    transaction_type TEXT DEFAULT 'payment', -- payment, donation, invoice, refund, subscription
    date            TEXT NOT NULL,
    status          TEXT DEFAULT 'completed', -- pending, completed, failed, refunded
    description     TEXT,
    project_id      TEXT REFERENCES projects(id),
    external_ref    TEXT                     -- reference to Stripe, PayPal, Wave, etc.
);

-- ============================================================
-- PIPELINES
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT PRIMARY KEY,
    pipeline_name   TEXT NOT NULL,
    status          TEXT DEFAULT 'running',
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    current_stage   TEXT,
    trigger_event   TEXT,
    task_id         TEXT REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(id),
    stage_name      TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    started_at      TEXT,
    completed_at    TEXT,
    output          TEXT,
    error           TEXT
);

-- ============================================================
-- SELF-IMPROVEMENT
-- ============================================================

CREATE TABLE IF NOT EXISTS friction_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at     TEXT NOT NULL,
    category        TEXT NOT NULL,             -- repeated_manual, error_pattern, slow_path, operator_correction
    description     TEXT NOT NULL,
    frequency       INTEGER DEFAULT 1,         -- how many times observed
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    source          TEXT,                       -- session analysis, error log, operator feedback
    status          TEXT DEFAULT 'open',        -- open, proposed, resolved, dismissed
    proposal_id     TEXT REFERENCES improvement_proposals(id)
);

CREATE TABLE IF NOT EXISTS improvement_proposals (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    friction_id     INTEGER REFERENCES friction_log(id),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    proposal_type   TEXT NOT NULL,              -- new_skill, new_pipeline, config_change, integration, architectural
    estimated_effort TEXT,                      -- low, medium, high
    estimated_value TEXT,                       -- low, medium, high, critical
    implementation  TEXT,                       -- JSON: what specifically to build/change
    status          TEXT DEFAULT 'proposed',    -- proposed, approved, implementing, completed, rejected, deferred
    deliberation_id TEXT REFERENCES deliberations(id),
    decided_by      TEXT,
    decided_at      TEXT,
    implemented_at  TEXT,
    verified        INTEGER DEFAULT 0,          -- 0/1: did the improvement actually reduce friction?
    verification_notes TEXT
);

CREATE TABLE IF NOT EXISTS deliberations (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    topic           TEXT NOT NULL,
    context         TEXT,                       -- JSON: what was known at deliberation time
    time_cap_seconds INTEGER DEFAULT 600,
    token_cap       INTEGER DEFAULT 50000,
    tokens_used     INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    perspectives    TEXT NOT NULL,              -- JSON array of perspective results
    recommendation  TEXT,
    confidence      REAL DEFAULT 0,
    dissenting_views TEXT,                      -- JSON array
    status          TEXT DEFAULT 'pending',     -- pending, in_progress, completed, timed_out
    decision_id     TEXT,                       -- links to vault decision if one was created
    triggered_by    TEXT                        -- operator, self_improvement, overnight
);

CREATE TABLE IF NOT EXISTS skill_proposals (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    trigger_phrases TEXT,                       -- JSON array
    required_tools  TEXT,                       -- JSON array
    estimated_value TEXT,                       -- low, medium, high
    detection_source TEXT,                      -- session_analysis, operator_request, template, friction
    evidence        TEXT,                       -- JSON: what triggered the detection (session IDs, friction IDs)
    status          TEXT DEFAULT 'proposed',    -- proposed, approved, building, deployed, rejected, failed
    skill_path      TEXT,                       -- path to the deployed skill if built
    deployed_at     TEXT,
    usage_count     INTEGER DEFAULT 0,
    success_rate    REAL DEFAULT 0
);

-- ============================================================
-- EXTERNAL INTELLIGENCE
-- ============================================================

CREATE TABLE IF NOT EXISTS intelligence_sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    layer           INTEGER NOT NULL,           -- 0-5 (internal through global)
    tier            TEXT NOT NULL,              -- api, firecrawl, social, restricted
    url             TEXT,                       -- base URL or API endpoint
    update_cadence  TEXT NOT NULL,              -- hourly, daily, weekly, on_demand
    last_checked    TEXT,
    last_success    TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    config          TEXT,                       -- JSON: auth, rate limits, selectors
    is_active       INTEGER DEFAULT 1,
    category        TEXT,                       -- weather, market, regulation, competitor, news, social
    project_id      TEXT                        -- scoped to a project, or NULL for global
);

CREATE TABLE IF NOT EXISTS intelligence_briefs (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES intelligence_sources(id),
    created_at      TEXT NOT NULL,
    layer           INTEGER NOT NULL,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,              -- LLM-synthesized analysis
    raw_data        TEXT,                       -- JSON: the raw data that was analyzed
    key_findings    TEXT,                       -- JSON array
    relevance_score REAL DEFAULT 0,             -- how relevant to operator's context
    expires_at      TEXT,                       -- when this brief is stale
    project_id      TEXT,
    surfaced        INTEGER DEFAULT 0,          -- 0/1: has this been shown to operator?
    surfaced_at     TEXT,
    operator_action TEXT                        -- acknowledged, acted_on, dismissed, NULL
);

-- ============================================================
-- METRICS
-- ============================================================

CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    value           REAL NOT NULL,
    unit            TEXT,
    project_id      TEXT,
    timestamp       TEXT NOT NULL,
    source          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    name            TEXT PRIMARY KEY,
    description     TEXT,
    target_value    REAL,
    target_unit     TEXT,
    direction       TEXT,
    category        TEXT
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_name);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_agent ON approvals(agent_id);

CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_type, from_id, link_type);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_type, to_id, link_type);
CREATE INDEX IF NOT EXISTS idx_links_type ON links(link_type);

CREATE INDEX IF NOT EXISTS idx_context_stale ON context_cards(stale_after);

CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(name, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_project ON metrics(project_id);

CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);

-- Areas
CREATE INDEX IF NOT EXISTS idx_areas_active ON areas(is_active);
CREATE INDEX IF NOT EXISTS idx_areas_parent ON areas(parent_id);

-- Workflows
CREATE INDEX IF NOT EXISTS idx_workflows_active ON workflows(is_active);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);

-- Pipeline entries
CREATE INDEX IF NOT EXISTS idx_pipeline_entries_person ON pipeline_entries(person_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_entries_pipeline ON pipeline_entries(pipeline_name);
CREATE INDEX IF NOT EXISTS idx_pipeline_entries_stage ON pipeline_entries(pipeline_name, stage);

-- Reminders
CREATE INDEX IF NOT EXISTS idx_reminders_person ON reminders(person_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);

-- Transactions
CREATE INDEX IF NOT EXISTS idx_transactions_person ON transactions(person_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_project ON transactions(project_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);

-- Procedures
CREATE INDEX IF NOT EXISTS idx_procedures_owner ON procedures(owner);
CREATE INDEX IF NOT EXISTS idx_procedures_review ON procedures(next_review);

-- Self-improvement
CREATE INDEX IF NOT EXISTS idx_friction_status ON friction_log(status);
CREATE INDEX IF NOT EXISTS idx_friction_category ON friction_log(category);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON improvement_proposals(status);
CREATE INDEX IF NOT EXISTS idx_deliberations_status ON deliberations(status);
CREATE INDEX IF NOT EXISTS idx_skill_proposals_status ON skill_proposals(status);

-- External intelligence
CREATE INDEX IF NOT EXISTS idx_sources_layer ON intelligence_sources(layer);
CREATE INDEX IF NOT EXISTS idx_sources_active ON intelligence_sources(is_active, update_cadence);
CREATE INDEX IF NOT EXISTS idx_briefs_created ON intelligence_briefs(created_at);
CREATE INDEX IF NOT EXISTS idx_briefs_layer ON intelligence_briefs(layer, category);
CREATE INDEX IF NOT EXISTS idx_briefs_project ON intelligence_briefs(project_id);
CREATE INDEX IF NOT EXISTS idx_briefs_unsurfaced ON intelligence_briefs(surfaced) WHERE surfaced = 0;

-- ============================================================
-- COMPANION SESSIONS (intelligence engine state)
-- ============================================================

CREATE TABLE IF NOT EXISTS companion_sessions (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'active',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    title           TEXT,
    transcript_json TEXT DEFAULT '[]',
    notes_json      TEXT DEFAULT '{}',
    research_json   TEXT DEFAULT '[]',
    cards_json      TEXT DEFAULT '[]',
    context_json    TEXT DEFAULT '{}',
    last_processed_index INTEGER DEFAULT 0,
    utterance_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS companion_session_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_data      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    sequence_num    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cse_session ON companion_session_events(session_id, sequence_num);

-- ============================================================
-- FULL-TEXT SEARCH
-- ============================================================

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    title, description, content=tasks, content_rowid=rowid
);
