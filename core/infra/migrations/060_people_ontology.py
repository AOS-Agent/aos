"""
Migration 060: People Ontology schema expansion.

(Renumbered from 028 during release-train wave 4 promotion — main had
already allocated numbers up through 059 by the time this transplant
landed. Also fixed here: check()/up() no longer treat a missing
people.db as "already applied" — see _ensure_people_db()'s docstring
for why that was a silent-failure bug, aos#153.)

Adds the relational backbone for identity resolution, source provenance,
and community detection to people.db:

- source_record: tracks where each piece of data originated
- contact_point: richer contact identifiers (replaces person_identifiers over time)
- organization: companies, schools, communities
- membership: person <-> organization links with roles
- circle: detected or manual communities / social groups
- circle_membership: person <-> circle links
- hygiene_queue: pending review items (merge, archive, normalize)
- hygiene_decision: audit log of queue resolutions
- deletion_tombstone: prevents re-import of deleted identifiers

Also adds golden_record_at and merge_target_id columns to the people table,
plus composite indexes for common query patterns.
"""

DESCRIPTION = "People ontology: source provenance, contact points, circles, orgs, hygiene queue"

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"


def _ensure_people_db() -> bool:
    """Ensure people.db exists, creating it via the framework's own
    db.connect() (which lazily applies schema.sql) if it doesn't.

    Without this, 8 of the 9 wave-4 migrations returned True from both
    check() and up() whenever DB_PATH didn't exist yet — intended as
    "nothing to migrate, will run next cycle" but the runner's watermark
    is a single monotonic integer (core/infra/migrations/runner.py), not
    per-migration state. Once a migration is marked applied it is never
    reconsidered, so on a machine where people.db is created lazily by
    runtime activity (core/engine/people/db.py's connect(), triggered by
    the first comms message), any of these migrations that ran *before*
    that first message permanently strands its tables — a quiet
    feature-shaped hole, not a crash. Calling connect() here removes the
    race entirely: the DB exists by the time this migration's own logic
    runs, every time. See aos#153.
    """
    # No file-exists early return: a partial/schema-less people.db (from an
    # interrupted run or a bare service touch) must still get the schema.
    # people_db.connect() gates on the 'people' TABLE and schema.sql is
    # fully idempotent (IF NOT EXISTS), so this is always safe. Clean-box
    # finding 2026-07-15 — same file-exists-vs-schema-exists class as
    # 053/058.
    core_dir = next((p for p in Path(__file__).resolve().parents if p.name == "core"), None)
    people_dir = core_dir / "engine" / "people" if core_dir else None
    if not people_dir or not people_dir.exists():
        return False
    if str(people_dir) not in sys.path:
        sys.path.insert(0, str(people_dir))
    try:
        import db as people_db
        people_db.connect().close()
    except Exception as e:
        print(f"  Could not lazily create people.db: {e}")
        return False
    return True


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def check() -> bool:
    """Return True if migration has already been applied."""
    if not _ensure_people_db():
        return False  # Can't verify yet — let up() try and report clearly
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Migration is applied if the core new table exists
        return _table_exists(conn, "contact_point")
    finally:
        conn.close()


def up() -> bool:
    """Create ontology tables, add columns, build indexes."""
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── New tables ──────────────────────────────────────────

        conn.executescript("""
            -- Source provenance: tracks where each piece of data came from
            CREATE TABLE IF NOT EXISTS source_record (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL REFERENCES people(id),
                source_type TEXT NOT NULL,
                source_id TEXT,
                raw_data TEXT,
                synced_at INTEGER NOT NULL,
                priority INTEGER DEFAULT 50,
                created_at INTEGER NOT NULL
            );

            -- Richer contact points (eventually replaces person_identifiers)
            CREATE TABLE IF NOT EXISTS contact_point (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL REFERENCES people(id),
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized TEXT,
                label TEXT,
                source_id TEXT REFERENCES source_record(id),
                is_primary INTEGER DEFAULT 0,
                is_shared INTEGER DEFAULT 0,
                verified_at INTEGER,
                created_at INTEGER NOT NULL,
                UNIQUE(person_id, type, normalized)
            );

            -- Organizations
            CREATE TABLE IF NOT EXISTS organization (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,
                domain TEXT,
                industry TEXT,
                city TEXT,
                country TEXT,
                parent_org_id TEXT REFERENCES organization(id),
                created_at INTEGER NOT NULL
            );

            -- Person <-> Organization membership
            CREATE TABLE IF NOT EXISTS membership (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL REFERENCES people(id),
                org_id TEXT NOT NULL REFERENCES organization(id),
                role TEXT,
                department TEXT,
                reports_to_id TEXT REFERENCES people(id),
                start_date TEXT,
                end_date TEXT,
                source TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(person_id, org_id, role)
            );

            -- Circles (detected communities or manual groups)
            CREATE TABLE IF NOT EXISTS circle (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                subcategory TEXT,
                source TEXT,
                wa_group_id TEXT,
                confidence REAL DEFAULT 1.0,
                resolution REAL,
                created_at INTEGER NOT NULL
            );

            -- Circle membership
            CREATE TABLE IF NOT EXISTS circle_membership (
                person_id TEXT NOT NULL REFERENCES people(id),
                circle_id TEXT NOT NULL REFERENCES circle(id),
                role_in_circle TEXT,
                confidence REAL DEFAULT 1.0,
                added_at INTEGER NOT NULL,
                source TEXT,
                PRIMARY KEY(person_id, circle_id)
            );

            -- Hygiene queue: pending review items
            CREATE TABLE IF NOT EXISTS hygiene_queue (
                id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                person_a_id TEXT REFERENCES people(id),
                person_b_id TEXT REFERENCES people(id),
                confidence REAL DEFAULT 0.0,
                reason TEXT,
                proposed_data TEXT,
                status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                resolved_at INTEGER
            );

            -- Hygiene decision audit log
            CREATE TABLE IF NOT EXISTS hygiene_decision (
                id TEXT PRIMARY KEY,
                queue_id TEXT NOT NULL REFERENCES hygiene_queue(id),
                decision TEXT NOT NULL,
                decided_by TEXT DEFAULT 'operator',
                notes TEXT,
                decided_at INTEGER NOT NULL
            );

            -- Tombstones for deleted identifiers (prevent re-import)
            CREATE TABLE IF NOT EXISTS deletion_tombstone (
                identifier_hash TEXT PRIMARY KEY,
                deleted_at INTEGER NOT NULL,
                reason TEXT
            );
        """)

        # ── New columns on people ────────────────────────────────

        if not _column_exists(conn, "people", "golden_record_at"):
            conn.execute("ALTER TABLE people ADD COLUMN golden_record_at INTEGER")
            print("  Added column people.golden_record_at")

        if not _column_exists(conn, "people", "merge_target_id"):
            conn.execute("ALTER TABLE people ADD COLUMN merge_target_id TEXT REFERENCES people(id)")
            print("  Added column people.merge_target_id")

        # ── Indexes ──────────────────────────────────────────────

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_source_record_person ON source_record(person_id);
            CREATE INDEX IF NOT EXISTS idx_source_record_type ON source_record(source_type);
            CREATE INDEX IF NOT EXISTS idx_contact_point_person ON contact_point(person_id);
            CREATE INDEX IF NOT EXISTS idx_contact_point_normalized ON contact_point(type, normalized);
            CREATE INDEX IF NOT EXISTS idx_membership_person ON membership(person_id);
            CREATE INDEX IF NOT EXISTS idx_membership_org ON membership(org_id);
            CREATE INDEX IF NOT EXISTS idx_circle_membership_person ON circle_membership(person_id);
            CREATE INDEX IF NOT EXISTS idx_circle_membership_circle ON circle_membership(circle_id);
            CREATE INDEX IF NOT EXISTS idx_hygiene_queue_status ON hygiene_queue(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_people_merge_target ON people(merge_target_id);
        """)

        conn.commit()
        print("  Migration 060 complete: People ontology schema created")
        return True

    except Exception as e:
        print(f"  Migration 060 failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()
