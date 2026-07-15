CREATE TABLE IF NOT EXISTS aliases (
    alias       TEXT NOT NULL,              -- "mom", "ballan", "debo"
    person_id   TEXT REFERENCES people(id),
    group_id    TEXT REFERENCES groups(id), -- for group aliases like "family group"
    type        TEXT NOT NULL,              -- relationship, nickname, short_name, group
    priority    INTEGER DEFAULT 0,          -- higher = checked first
    created_at  INTEGER,
    PRIMARY KEY (alias)
);

CREATE TABLE IF NOT EXISTS communication_patterns (
    person_id               TEXT PRIMARY KEY REFERENCES people(id),
    avg_response_time_mins  REAL,
    p50_response_mins       REAL,
    p90_response_mins       REAL,
    preferred_hours         TEXT,
    preferred_days          TEXT,
    style_brief_ratio       REAL,
    avg_message_length      REAL,
    language                TEXT,
    sample_size             INTEGER,
    computed_at             INTEGER
);

CREATE TABLE IF NOT EXISTS contact_metadata (
    person_id           TEXT PRIMARY KEY REFERENCES people(id),
    -- Personal
    birthday            TEXT,               -- YYYY-MM-DD
    birthday_source     TEXT,               -- contacts, social, manual
    organization        TEXT,
    job_title           TEXT,
    city                TEXT,
    country             TEXT,
    how_met             TEXT,
    met_date            TEXT,
    cultural_notes      TEXT,
    -- Family connections
    spouse_id           TEXT REFERENCES people(id),
    children_names      TEXT,               -- JSON array
    -- Communication
    preferred_channel   TEXT,               -- whatsapp, imessage, email, sms
    communication_style TEXT,               -- brief, detailed, voice_notes, etc.
    best_contact_time   TEXT,
    language_preference TEXT,
    -- Online presence
    linkedin_url        TEXT,
    github_url          TEXT,
    twitter_handle      TEXT,
    website             TEXT,
    -- Operator notes
    notes               TEXT,
    last_manual_update  INTEGER
);

CREATE TABLE IF NOT EXISTS dedup_log (
    id              TEXT PRIMARY KEY,
    action          TEXT NOT NULL,          -- merge, skip, flag
    primary_id      TEXT,                   -- person kept
    secondary_id    TEXT,                   -- person merged/removed
    reason          TEXT,
    confidence      REAL,
    decided_at      INTEGER,
    decided_by      TEXT                    -- operator, auto
);

CREATE TABLE IF NOT EXISTS enrichment_cache (
    person_id   TEXT NOT NULL REFERENCES people(id),
    source      TEXT NOT NULL,             -- linkedin, google, github, website
    fetched_at  INTEGER NOT NULL,
    expires_at  INTEGER,
    status      TEXT,                      -- success, failed, pending_approval
    data        TEXT,                      -- JSON blob
    approved    INTEGER DEFAULT 0,
    PRIMARY KEY (person_id, source)
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id    TEXT NOT NULL REFERENCES groups(id),
    person_id   TEXT REFERENCES people(id), -- NULL if unresolved member
    wa_jid      TEXT,                       -- WhatsApp JID for unresolved members
    name        TEXT,                       -- Display name (for unresolved)
    role        TEXT,                       -- admin, member, etc.
    active      INTEGER DEFAULT 1,
    joined_at   TEXT,
    UNIQUE (group_id, person_id, wa_jid)
);

CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT,                       -- family, community, business, social, project
    wa_jid      TEXT,                       -- WhatsApp group JID if applicable
    description TEXT,
    member_count INTEGER DEFAULT 0,
    created_at  INTEGER,
    updated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS intelligence_queue (
    id              TEXT PRIMARY KEY,
    person_id       TEXT REFERENCES people(id),
    surface_type    TEXT NOT NULL,          -- birthday, drift_nudge, life_event_followup, meeting_brief, opportunity, reconnect, care_prompt
    priority        INTEGER DEFAULT 3,
    surface_after   INTEGER,               -- don't show before this time
    surfaced_at     INTEGER,               -- when shown to operator
    status          TEXT DEFAULT 'pending', -- pending, surfaced, dismissed, acted
    content         TEXT,
    context_json    TEXT,
    created_at      INTEGER,
    expires_at      INTEGER
);

CREATE TABLE IF NOT EXISTS interactions (
    id          TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL REFERENCES people(id),
    occurred_at INTEGER NOT NULL,
    channel     TEXT NOT NULL,              -- whatsapp, imessage, email, call, meeting, mention
    direction   TEXT,                       -- inbound, outbound, both
    msg_count   INTEGER,
    subject     TEXT,
    summary     TEXT,                       -- AI-generated
    sentiment   REAL,                       -- -1.0 to 1.0
    topics      TEXT,                       -- JSON array
    raw_ref     TEXT,                       -- pointer to source data
    indexed_at  INTEGER
);

CREATE TABLE IF NOT EXISTS life_events (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL REFERENCES people(id),
    event_type      TEXT NOT NULL,          -- job_change, marriage, baby, move, graduation, illness, achievement, death_in_family
    event_date      TEXT,
    description     TEXT,
    detected_from   TEXT,                   -- whatsapp, email, manual, voice_memo
    detected_at     INTEGER,
    follow_up_sent  INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id              TEXT PRIMARY KEY,        -- nanoid: "p_a7f3k2"
    canonical_name  TEXT NOT NULL,           -- "Ahmad Ballan"
    display_name    TEXT,                    -- preferred short name: "Ballan"
    first_name      TEXT,
    last_name       TEXT,
    nickname        TEXT,
    importance      INTEGER DEFAULT 3,      -- 1=inner circle, 2=active, 3=acquaintance, 4=peripheral
    privacy_level   INTEGER DEFAULT 1,      -- 1=full AI, 2=limited, 3=no AI analysis
    profile_version INTEGER DEFAULT 0,
    is_archived     INTEGER DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS person_identifiers (
    person_id   TEXT NOT NULL REFERENCES people(id),
    type        TEXT NOT NULL,              -- phone, email, wa_jid, twitter, linkedin, github
    value       TEXT NOT NULL,
    normalized  TEXT,                       -- phone digits only, email lowercase, etc.
    is_primary  INTEGER DEFAULT 0,
    source      TEXT,                       -- mac_contacts, whatsapp, imessage, manual
    label       TEXT,                       -- Mobile, Home, Work, Dubai, Pakistan, etc.
    added_at    INTEGER,
    PRIMARY KEY (person_id, type, value)
);

CREATE TABLE IF NOT EXISTS profile_versions (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL REFERENCES people(id),
    version         INTEGER NOT NULL,
    generated_at    INTEGER NOT NULL,
    model           TEXT,
    trigger         TEXT,                   -- new_messages, manual, scheduled
    profile_json    TEXT NOT NULL,
    vault_path      TEXT
);

CREATE TABLE IF NOT EXISTS relationship_state (
    person_id               TEXT PRIMARY KEY REFERENCES people(id),
    last_interaction_at     INTEGER,
    last_interaction_channel TEXT,
    avg_days_between        REAL,           -- rolling 90-day baseline
    interaction_count_7d    INTEGER DEFAULT 0,
    interaction_count_30d   INTEGER DEFAULT 0,
    interaction_count_90d   INTEGER DEFAULT 0,
    msg_count_30d           INTEGER DEFAULT 0,
    outbound_30d            INTEGER DEFAULT 0,
    inbound_30d             INTEGER DEFAULT 0,
    days_since_contact      INTEGER,
    drift_threshold_days    INTEGER,
    drift_alert_sent_at     INTEGER,
    recent_topics           TEXT,           -- JSON: top 5 last 30d
    historic_topics         TEXT,           -- JSON: top 5 31-180d
    trajectory              TEXT,           -- growing, stable, drifting, dormant
    trajectory_updated_at   INTEGER,
    computed_at             INTEGER
);

CREATE TABLE IF NOT EXISTS relationships (
    person_a_id     TEXT NOT NULL REFERENCES people(id),
    person_b_id     TEXT NOT NULL REFERENCES people(id),
    type            TEXT NOT NULL,          -- family, friend, colleague, community, business, acquaintance
    subtype         TEXT,                   -- spouse, sibling, parent, child, mentor, client, classmate
    strength        REAL DEFAULT 0.5,       -- 0.0 to 1.0
    source          TEXT,                   -- contacts_db, whatsapp_group, calendar, manual
    context         TEXT,                   -- "both in Quran Garden", "introduced by Ahmad"
    since           TEXT,                   -- date
    notes           TEXT,
    created_at      INTEGER,
    updated_at      INTEGER,
    PRIMARY KEY (person_a_id, person_b_id, type)
);

CREATE TABLE IF NOT EXISTS surface_feedback (
    id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES people(id),
    surface_type TEXT NOT NULL,
    surface_at INTEGER NOT NULL,
    operator_action TEXT,
    action_at INTEGER,
    original_content TEXT,
    final_content TEXT,
    session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_aliases_person ON aliases(person_id);

CREATE INDEX IF NOT EXISTS idx_feedback_person ON surface_feedback(person_id, surface_type, surface_at DESC);

CREATE INDEX IF NOT EXISTS idx_groups_name ON groups(name);

CREATE INDEX IF NOT EXISTS idx_identifiers_normalized ON person_identifiers(normalized);

CREATE INDEX IF NOT EXISTS idx_identifiers_value ON person_identifiers(value);

CREATE INDEX IF NOT EXISTS idx_interactions_channel ON interactions(channel, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_person ON interactions(person_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_life_events_person ON life_events(person_id, event_date DESC);

CREATE INDEX IF NOT EXISTS idx_people_importance ON people(importance);

CREATE INDEX IF NOT EXISTS idx_people_name ON people(canonical_name);

CREATE INDEX IF NOT EXISTS idx_profile_versions ON profile_versions(person_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_queue_pending ON intelligence_queue(status, surface_after, priority);

CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(person_a_id);

CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(person_b_id);

CREATE TABLE IF NOT EXISTS style_profiles (
    person_id         TEXT PRIMARY KEY REFERENCES people(id),
    computed_at       INTEGER NOT NULL,
    sample_size       INTEGER NOT NULL,
    recompute_after   INTEGER NOT NULL,
    language_mix      TEXT,
    romanized_ar      INTEGER DEFAULT 0,
    signs_off         INTEGER DEFAULT 0,
    uses_periods      INTEGER DEFAULT 0,
    response_style    TEXT,
    enhancement_default TEXT DEFAULT 'clean',
    prose_summary     TEXT,
    style_markers     TEXT
);

CREATE TABLE IF NOT EXISTS style_modes (
    person_id          TEXT NOT NULL REFERENCES people(id),
    mode_name          TEXT NOT NULL,
    weight             REAL NOT NULL,
    signature          TEXT NOT NULL,
    exemplar_ids       TEXT NOT NULL,
    topic_correlations TEXT,
    PRIMARY KEY (person_id, mode_name)
);

CREATE INDEX IF NOT EXISTS idx_style_profiles_recompute ON style_profiles(recompute_after);

CREATE INDEX IF NOT EXISTS idx_style_modes_person ON style_modes(person_id);
