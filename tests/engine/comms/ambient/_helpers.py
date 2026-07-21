"""Fixtures for the ambient (Phase 5) tests.

Builds an isolated comms.db (real `messages` table + frozen `message_entities`
from migration 084) and a minimal people.db (people + intelligence_queue +
surface_feedback), so every test runs against the DDL that ships. All data is
synthetic — no real message content ever touches these tests.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
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

_PEOPLE_SCHEMA = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, canonical_name TEXT, first_name TEXT, last_name TEXT,
    display_name TEXT, privacy_level INTEGER DEFAULT 1, importance INTEGER DEFAULT 3,
    is_archived INTEGER DEFAULT 0, is_self INTEGER DEFAULT 0
);
CREATE TABLE aliases (person_id TEXT, alias TEXT);
CREATE TABLE intelligence_queue (
    id TEXT PRIMARY KEY, person_id TEXT, surface_type TEXT NOT NULL,
    priority INTEGER DEFAULT 3, surface_after INTEGER, surfaced_at INTEGER,
    status TEXT DEFAULT 'pending', content TEXT, context_json TEXT,
    created_at INTEGER, expires_at INTEGER
);
CREATE TABLE surface_feedback (
    id TEXT PRIMARY KEY, person_id TEXT, surface_type TEXT NOT NULL,
    surface_at INTEGER NOT NULL, operator_action TEXT, action_at INTEGER,
    original_content TEXT, final_content TEXT, session_id TEXT
);
"""


def _frozen_sql() -> str:
    spec = importlib.util.spec_from_file_location(
        "mig084", _REPO / "core" / "infra" / "migrations" / "084_message_entities_frozen.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m._FROZEN_SQL


def make_comms_db(path: Path, messages=None, entities=None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_MESSAGES_SCHEMA)
    conn.executescript(_frozen_sql())
    for m in messages or []:
        conn.execute(
            "INSERT INTO messages(id, channel, direction, content, timestamp, person_id)"
            " VALUES (:id,:channel,:direction,:content,:timestamp,:person_id)",
            {"channel": "whatsapp", "person_id": None, **m})
    for e in entities or []:
        conn.execute(
            "INSERT INTO message_entities(id, entity_type, value, fields_json, confidence,"
            " source_ids, person_id, channel, batch_key, extractor_version, model,"
            " created_at, ontology_type, ontology_id, status)"
            " VALUES (:id,:entity_type,:value,:fields_json,:confidence,:source_ids,"
            ":person_id,:channel,:batch_key,:extractor_version,:model,:created_at,"
            ":ontology_type,:ontology_id,:status)",
            {"channel": "whatsapp", "batch_key": "b1", "extractor_version": "extract@1",
             "model": "haiku", "created_at": "2026-07-10T00:00:00", "person_id": None,
             "ontology_type": None, "ontology_id": None, "status": "active", **e})
    conn.commit()
    conn.close()
    return path


def msg(mid, content, *, ts, direction="inbound", person_id=None, channel="whatsapp"):
    return {"id": mid, "channel": channel, "direction": direction,
            "content": content, "timestamp": ts, "person_id": person_id}


def entity(eid, etype, *, fields, conf=0.9, source_ids, person_id=None,
           status="active", ontology_id=None):
    value = fields.get("what") or fields.get("value") or fields.get("merchant") or etype
    return {"id": eid, "entity_type": etype, "value": value,
            "fields_json": json.dumps(fields), "confidence": conf,
            "source_ids": json.dumps(source_ids), "person_id": person_id,
            "status": status, "ontology_id": ontology_id}


def make_people_db(path: Path, people=None, nudges=None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_PEOPLE_SCHEMA)
    for p in people or []:
        conn.execute(
            "INSERT INTO people(id, canonical_name, first_name, privacy_level,"
            " importance, is_self) VALUES (:id,:canonical_name,:first_name,"
            ":privacy_level,:importance,:is_self)",
            {"first_name": None, "privacy_level": 1, "importance": 3, "is_self": 0, **p})
    for n in nudges or []:
        conn.execute(
            "INSERT INTO intelligence_queue(id, person_id, surface_type, priority,"
            " surface_after, surfaced_at, status, content, context_json, created_at,"
            " expires_at) VALUES (:id,:person_id,:surface_type,:priority,:surface_after,"
            ":surfaced_at,:status,:content,:context_json,:created_at,:expires_at)",
            {"priority": 3, "surface_after": 0, "surfaced_at": None, "status": "pending",
             "content": "", "context_json": "{}", "created_at": int(time.time()),
             "expires_at": None, "person_id": None, **n})
    conn.commit()
    conn.close()
    return path
