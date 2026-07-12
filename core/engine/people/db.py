"""Thin database access layer for People Intelligence.

All queries go through this module. Handles connection, schema init,
and common operations. The DB lives at ~/.aos/data/people.db.
"""

import json
import os
import sqlite3
import string
import random
import time
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _nanoid(prefix: str = "p", size: int = 8) -> str:
    """Generate a short unique ID."""
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(random.choices(chars, k=size))
    return f"{prefix}_{suffix}"


def connect() -> sqlite3.Connection:
    """Get a connection to the people DB. Creates schema if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Init schema if tables don't exist
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "people" not in tables:
        conn.executescript(SCHEMA_PATH.read_text())

    return conn


def now_ts() -> int:
    """Current unix timestamp."""
    return int(time.time())


# ── People CRUD ──────────────────────────────────────────────

def insert_person(conn: sqlite3.Connection, *,
                  name: str, first: str = "", last: str = "",
                  nickname: str = "", display_name: str = "",
                  importance: int = 3) -> str:
    """Insert a person and return their ID."""
    pid = _nanoid("p")
    ts = now_ts()
    conn.execute("""
        INSERT INTO people (id, canonical_name, display_name, first_name, last_name,
                           nickname, importance, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pid, name, display_name or None, first, last, nickname or None, importance, ts, ts))
    return pid


def add_identifier(conn: sqlite3.Connection, person_id: str, *,
                   type: str, value: str, normalized: str = "",
                   is_primary: int = 0, source: str = "", label: str = ""):
    """Add an identifier (phone, email, wa_jid, etc.) to a person."""
    conn.execute("""
        INSERT OR IGNORE INTO person_identifiers
        (person_id, type, value, normalized, is_primary, source, label, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (person_id, type, value, normalized, is_primary, source, label, now_ts()))


def set_metadata(conn: sqlite3.Connection, person_id: str, **fields):
    """Set or update contact metadata fields."""
    existing = conn.execute(
        "SELECT person_id FROM contact_metadata WHERE person_id = ?", (person_id,)
    ).fetchone()

    if existing:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [person_id]
        conn.execute(f"UPDATE contact_metadata SET {sets} WHERE person_id = ?", vals)
    else:
        fields["person_id"] = person_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO contact_metadata ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )


def insert_group(conn: sqlite3.Connection, *,
                 name: str, type: str = "", wa_jid: str = "",
                 member_count: int = 0) -> str:
    """Insert a group and return its ID."""
    gid = _nanoid("g")
    ts = now_ts()
    conn.execute("""
        INSERT INTO groups (id, name, type, wa_jid, member_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (gid, name, type or None, wa_jid or None, member_count, ts, ts))
    return gid


def add_group_member(conn: sqlite3.Connection, group_id: str, *,
                     person_id: str = None, wa_jid: str = "",
                     name: str = "", role: str = "member"):
    """Add a member to a group."""
    conn.execute("""
        INSERT OR IGNORE INTO group_members (group_id, person_id, wa_jid, name, role)
        VALUES (?, ?, ?, ?, ?)
    """, (group_id, person_id, wa_jid or None, name or None, role))


def set_relationship_state(conn: sqlite3.Connection, person_id: str, **fields):
    """Set or update relationship state metrics."""
    existing = conn.execute(
        "SELECT person_id FROM relationship_state WHERE person_id = ?", (person_id,)
    ).fetchone()

    fields["computed_at"] = now_ts()

    if existing:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [person_id]
        conn.execute(f"UPDATE relationship_state SET {sets} WHERE person_id = ?", vals)
    else:
        fields["person_id"] = person_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO relationship_state ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )


def insert_relationship(conn: sqlite3.Connection, *,
                        person_a: str, person_b: str,
                        type: str, subtype: str = "",
                        source: str = "", context: str = "",
                        strength: float = 0.5):
    """Insert a relationship between two people."""
    ts = now_ts()
    conn.execute("""
        INSERT OR IGNORE INTO relationships
        (person_a_id, person_b_id, type, subtype, strength, source, context, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (person_a, person_b, type, subtype or None, strength, source, context, ts, ts))


# ── Lookups ──────────────────────────────────────────────────

def find_person_by_identifier(conn: sqlite3.Connection,
                              type: str, normalized: str) -> dict | None:
    """Find a person by a normalized identifier."""
    row = conn.execute("""
        SELECT p.* FROM people p
        JOIN person_identifiers pi ON pi.person_id = p.id
        WHERE pi.type = ? AND pi.normalized = ?
        LIMIT 1
    """, (type, normalized)).fetchone()
    return dict(row) if row else None


def find_person_by_name(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Find people by name (case-insensitive substring)."""
    rows = conn.execute("""
        SELECT * FROM people WHERE canonical_name LIKE ? COLLATE NOCASE
    """, (f"%{name}%",)).fetchall()
    return [dict(r) for r in rows]


def get_person(conn: sqlite3.Connection, person_id: str) -> dict | None:
    """Get a person by ID with all metadata."""
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if not row:
        return None

    person = dict(row)

    # Add identifiers
    ids = conn.execute(
        "SELECT * FROM person_identifiers WHERE person_id = ?", (person_id,)
    ).fetchall()
    person["identifiers"] = [dict(i) for i in ids]

    # Add metadata
    meta = conn.execute(
        "SELECT * FROM contact_metadata WHERE person_id = ?", (person_id,)
    ).fetchone()
    if meta:
        person["metadata"] = dict(meta)

    # Add relationship state
    state = conn.execute(
        "SELECT * FROM relationship_state WHERE person_id = ?", (person_id,)
    ).fetchone()
    if state:
        person["state"] = dict(state)

    # Add contact points (new ontology table)
    try:
        cps = conn.execute(
            "SELECT * FROM contact_point WHERE person_id = ?", (person_id,)
        ).fetchall()
        person["contact_points"] = [dict(c) for c in cps]
    except sqlite3.OperationalError:
        pass

    # Add circle memberships (new ontology tables)
    try:
        circles = conn.execute("""
            SELECT c.name, c.category, cm.role_in_circle
            FROM circle_member cm
            JOIN circle c ON c.id = cm.circle_id
            WHERE cm.person_id = ?
        """, (person_id,)).fetchall()
        person["circles"] = [dict(c) for c in circles]
    except sqlite3.OperationalError:
        pass

    # Add organization memberships (new ontology tables)
    try:
        orgs = conn.execute("""
            SELECT o.name, m.role
            FROM membership m
            JOIN organization o ON o.id = m.org_id
            WHERE m.person_id = ?
        """, (person_id,)).fetchall()
        person["organizations"] = [dict(o) for o in orgs]
    except sqlite3.OperationalError:
        pass

    return person


# ── Contact Points ──────────────────────────────────────────

def insert_contact_point(conn: sqlite3.Connection, *,
                         person_id: str, type: str, value: str,
                         normalized: str = "", label: str = "",
                         source_id: str = None, is_primary: int = 0,
                         is_shared: int = 0) -> str:
    """Insert a contact point. Returns the ID."""
    cpid = _nanoid("cp")
    conn.execute("""
        INSERT OR IGNORE INTO contact_point
        (id, person_id, type, value, normalized, label, source_id, is_primary, is_shared, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (cpid, person_id, type, value, normalized, label, source_id, is_primary, is_shared, now_ts()))
    return cpid


# ── Source Records ──────────────────────────────────────────

def insert_source_record(conn: sqlite3.Connection, *,
                         person_id: str, source_type: str,
                         source_id: str = "", raw_data=None,
                         priority: int = 50) -> str:
    """Insert a source provenance record. Returns the ID."""
    srid = _nanoid("sr")
    raw_json = json.dumps(raw_data) if isinstance(raw_data, dict) else raw_data
    conn.execute("""
        INSERT INTO source_record
        (id, person_id, source_type, source_id, raw_data, priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (srid, person_id, source_type, source_id, raw_json, priority, now_ts()))
    return srid


# ── Circles ─────────────────────────────────────────────────

def insert_circle(conn: sqlite3.Connection, *,
                  name: str, category: str = "", subcategory: str = "",
                  source: str = "manual", wa_group_id: str = None,
                  confidence: float = 1.0, resolution: str = None) -> str:
    """Insert a circle (community). Returns the ID."""
    cid = _nanoid("ci")
    ts = now_ts()
    conn.execute("""
        INSERT INTO circle
        (id, name, category, subcategory, source, wa_group_id, confidence, resolution, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (cid, name, category, subcategory, source, wa_group_id, confidence, resolution, ts, ts))
    return cid


def add_circle_member(conn: sqlite3.Connection, *,
                      person_id: str, circle_id: str,
                      role_in_circle: str = "member",
                      confidence: float = 1.0, source: str = "manual"):
    """Add a person to a circle."""
    conn.execute("""
        INSERT OR IGNORE INTO circle_member
        (person_id, circle_id, role_in_circle, confidence, source, added_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (person_id, circle_id, role_in_circle, confidence, source, now_ts()))


# ── Organizations ───────────────────────────────────────────

def insert_organization(conn: sqlite3.Connection, *,
                        name: str, type: str = "company",
                        domain: str = None, industry: str = None,
                        city: str = None, country: str = None,
                        parent_org_id: str = None) -> str:
    """Insert an organization. Returns the ID."""
    oid = _nanoid("org")
    ts = now_ts()
    conn.execute("""
        INSERT INTO organization
        (id, name, type, domain, industry, city, country, parent_org_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (oid, name, type, domain, industry, city, country, parent_org_id, ts, ts))
    return oid


def insert_membership(conn: sqlite3.Connection, *,
                      person_id: str, org_id: str,
                      role: str = None, department: str = None,
                      reports_to_id: str = None,
                      start_date: str = None, end_date: str = None,
                      source: str = "") -> str:
    """Insert an org membership. Returns the ID."""
    mid = _nanoid("mem")
    ts = now_ts()
    conn.execute("""
        INSERT INTO membership
        (id, person_id, org_id, role, department, reports_to_id, start_date, end_date, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (mid, person_id, org_id, role, department, reports_to_id, start_date, end_date, source, ts))
    return mid


# ── Hygiene ─────────────────────────────────────────────────

def insert_hygiene_issue(conn: sqlite3.Connection, *,
                         action_type: str, person_a_id: str = None,
                         person_b_id: str = None, confidence: float = 0.0,
                         reason: str = "", proposed_data=None) -> str:
    """Insert a hygiene queue item. Returns the ID."""
    hqid = _nanoid("hq")
    proposed_json = json.dumps(proposed_data) if isinstance(proposed_data, dict) else proposed_data
    conn.execute("""
        INSERT INTO hygiene_queue
        (id, action_type, person_a_id, person_b_id, confidence, reason, proposed_data, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (hqid, action_type, person_a_id, person_b_id, confidence, reason, proposed_json, now_ts()))
    return hqid


def resolve_hygiene_issue(conn: sqlite3.Connection, *,
                          queue_id: str, decision: str,
                          decided_by: str = "operator", notes: str = ""):
    """Resolve a hygiene queue item (approve/reject)."""
    ts = now_ts()
    conn.execute("""
        UPDATE hygiene_queue SET status = ?, resolved_at = ? WHERE id = ?
    """, (decision, ts, queue_id))

    hdid = _nanoid("hd")
    conn.execute("""
        INSERT INTO hygiene_decision
        (id, queue_id, decision, decided_by, notes, decided_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (hdid, queue_id, decision, decided_by, notes, ts))


def stats(conn: sqlite3.Connection) -> dict:
    """Get database statistics."""
    result = {
        "people": conn.execute("SELECT COUNT(*) FROM people").fetchone()[0],
        "identifiers": conn.execute("SELECT COUNT(*) FROM person_identifiers").fetchone()[0],
        "groups": conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
        "group_members": conn.execute("SELECT COUNT(*) FROM group_members").fetchone()[0],
        "relationships": conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
        "interactions": conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0],
        "life_events": conn.execute("SELECT COUNT(*) FROM life_events").fetchone()[0],
        "with_birthday": conn.execute(
            "SELECT COUNT(*) FROM contact_metadata WHERE birthday IS NOT NULL"
        ).fetchone()[0],
        "with_metadata": conn.execute("SELECT COUNT(*) FROM contact_metadata").fetchone()[0],
    }

    # New ontology tables (gracefully handle if not yet created)
    for key, query in [
        ("contact_points", "SELECT COUNT(*) FROM contact_point"),
        ("source_records", "SELECT COUNT(*) FROM source_record"),
        ("circles", "SELECT COUNT(*) FROM circle"),
        ("organizations", "SELECT COUNT(*) FROM organization"),
        ("hygiene_pending", "SELECT COUNT(*) FROM hygiene_queue WHERE status = 'pending'"),
    ]:
        try:
            result[key] = conn.execute(query).fetchone()[0]
        except sqlite3.OperationalError:
            result[key] = 0

    return result
