"""Shared test scaffolding for the enrichment engine.

Builds an isolated comms.db that mirrors production: the real `messages` table
(+ FTS triggers) plus the frozen `message_entities` / `message_extraction`
schema pulled straight from migration 084, so tests run against the exact DDL
that ships. No real message content — all synthetic, reserved-domain data.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]

_MESSAGES_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, channel TEXT NOT NULL, direction TEXT NOT NULL,
    sender_id TEXT, recipient_id TEXT, content TEXT, timestamp TEXT NOT NULL,
    thread_id TEXT, reply_to_id TEXT, has_attachment INTEGER DEFAULT 0,
    attachment_type TEXT, attachment_path TEXT, processed INTEGER DEFAULT 0,
    channel_metadata TEXT, person_id TEXT, conversation_id TEXT,
    intent TEXT, urgency INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE messages_fts USING fts5(content);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


def _frozen_sql() -> str:
    spec = importlib.util.spec_from_file_location(
        "mig084", _REPO / "core" / "infra" / "migrations" / "084_message_entities_frozen.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m._FROZEN_SQL


def make_comms_db(path: Path, messages: list[dict] | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_MESSAGES_SCHEMA)
    conn.executescript(_frozen_sql())
    if messages:
        for m in messages:
            conn.execute(
                "INSERT INTO messages(id, channel, direction, sender_id, recipient_id,"
                " content, timestamp, person_id, channel_metadata)"
                " VALUES (:id,:channel,:direction,:sender_id,:recipient_id,:content,"
                ":timestamp,:person_id,:channel_metadata)",
                {"sender_id": None, "recipient_id": None, "person_id": None,
                 "channel_metadata": None, **m})
    conn.commit()
    conn.close()
    return path


def msg(mid, content, *, ts, channel="whatsapp", direction="inbound",
        person_id=None, sender_id=None, recipient_id=None, channel_metadata=None):
    return {"id": mid, "channel": channel, "direction": direction,
            "sender_id": sender_id, "recipient_id": recipient_id,
            "content": content, "timestamp": ts, "person_id": person_id,
            "channel_metadata": channel_metadata}
