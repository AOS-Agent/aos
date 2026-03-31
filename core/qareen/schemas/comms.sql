-- comms.sql — Cross-channel message store (comms.db)
-- Qareen communication layer: unified inbound/outbound message history
-- across telegram, whatsapp, email, slack, sms channels.

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    person_id       TEXT,                   -- resolved FK to people.db
    name            TEXT,
    status          TEXT DEFAULT 'open',    -- open, snoozed, archived
    last_message_at TEXT,
    message_count   INTEGER DEFAULT 0,
    unread_count    INTEGER DEFAULT 0,
    metadata        TEXT                    -- JSON
);

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    channel           TEXT NOT NULL,        -- telegram/whatsapp/email/slack/sms
    direction         TEXT NOT NULL,        -- inbound/outbound
    sender_id         TEXT,
    recipient_id      TEXT,
    content           TEXT,
    timestamp         TEXT NOT NULL,        -- ISO8601
    thread_id         TEXT,
    reply_to_id       TEXT,
    has_attachment    INTEGER NOT NULL DEFAULT 0,  -- 0/1
    attachment_type   TEXT,
    attachment_path   TEXT,
    processed         INTEGER NOT NULL DEFAULT 0,  -- 0/1
    channel_metadata  TEXT,                -- JSON
    person_id         TEXT,                -- resolved FK to people.db
    conversation_id   TEXT REFERENCES conversations(id),
    intent            TEXT,
    urgency           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS message_entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    TEXT NOT NULL,
    entity_type   TEXT NOT NULL,           -- person/project/topic
    entity_id     TEXT NOT NULL,
    confidence    REAL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_timestamp   ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_sender_id   ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_channel     ON messages(channel);
CREATE INDEX IF NOT EXISTS idx_messages_thread_id   ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_processed   ON messages(processed);
CREATE INDEX IF NOT EXISTS idx_messages_person      ON messages(person_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_intent      ON messages(intent);

CREATE INDEX IF NOT EXISTS idx_conversations_person  ON conversations(person_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status  ON conversations(status);
CREATE INDEX IF NOT EXISTS idx_conversations_channel ON conversations(channel);
