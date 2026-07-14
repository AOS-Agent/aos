"""
Migration 066: Create WhatsApp LID cache table.

(Renumbered from 042 during release-train wave 4 promotion. Hardened
during transplant: the CREATE statements are now wrapped in try/except
with rollback, matching 060/061/062/067's pattern.)

WhatsApp group members use Linked Device IDs (LIDs) instead of phone-based
JIDs. The whatsmeow bridge can resolve LID→JID, but this requires the bridge
to be running. The cache persists these mappings so resolution works even
when the bridge is temporarily down.

Idempotent: re-running is safe.
"""

DESCRIPTION = "Create wa_lid_cache table for WhatsApp LID-to-JID mapping"

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "people.db"


def _ensure_people_db() -> bool:
    """Ensure people.db exists, creating it via the framework's own
    db.connect() if it doesn't. See 060_people_ontology.py's copy of this
    helper for the full rationale (aos#153)."""
    if DB_PATH.exists():
        return True
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


def check() -> bool:
    if not _ensure_people_db():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        return "wa_lid_cache" in tables
    finally:
        conn.close()


def up() -> bool:
    if not _ensure_people_db():
        print(f"  Could not create/locate people.db at {DB_PATH} — aborting")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wa_lid_cache (
                lid TEXT PRIMARY KEY,
                jid TEXT NOT NULL,
                cached_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lid_jid ON wa_lid_cache(jid)")
        conn.commit()
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if check():
        print("Migration 066 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
