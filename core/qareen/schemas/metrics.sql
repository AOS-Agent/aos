-- metrics.sql — KPIs and performance data (metrics.db)
-- Qareen metrics layer: time-series KPIs, metric definitions, and context cards.

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT,
    project     TEXT,
    timestamp   TEXT NOT NULL,              -- ISO8601
    source      TEXT NOT NULL               -- manual/computed/imported
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    name          TEXT PRIMARY KEY,
    description   TEXT,
    target_value  REAL,
    target_unit   TEXT,
    direction     TEXT,                     -- up/down (higher or lower is better)
    category      TEXT                      -- financial/operational/engagement/growth
);

CREATE TABLE IF NOT EXISTS context_cards (
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    summary         TEXT,
    key_facts       TEXT,                   -- JSON
    recent_activity TEXT,                   -- JSON
    open_items      TEXT,                   -- JSON
    built_at        TEXT NOT NULL,          -- ISO8601
    stale_after     TEXT,                   -- ISO8601
    PRIMARY KEY (entity_type, entity_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp  ON metrics(name, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_project         ON metrics(project);
CREATE INDEX IF NOT EXISTS idx_metric_definitions_category ON metric_definitions(category);
