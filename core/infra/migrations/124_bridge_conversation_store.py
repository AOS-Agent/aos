"""
Migration 124: create the bridge's own conversation store (aos#235).

`core/services/bridge/activity_client.py` was a no-op from the Qareen
decommission (`f065151`) onward: four functions that returned `None`, called by
every bridge handler on every message. The rows used to go to a dashboard whose
tables were removed with the rest of Qareen, and the aos-app activity feed the
stubs were a placeholder for is gone too — the desktop app was retired in
migration 118. So for five months the bridge recorded nothing at all:
`~/.aos/data/dashboard/activity.db` stops at 2026-04-06, and "what did I
actually send and receive" had no answer.

The bridge now owns its store — `~/.aos/data/bridge.db`, one `messages` table,
no service and no network (`core/services/bridge/conversation_store.py`). Phone
numbers and email addresses are redacted before the row is written, so the file
cannot become a shadow copy of the operator's contacts.

This migration is the instance-layer half of that change, per the
component-lifecycle rule: the code self-heals (`ensure_schema()` runs on every
write), but a machine should not have to wait for its first Telegram message of
the day to acquire the file. Creating it here also means the quiet-hours queue
(`core/engine/notify/router.py`) finds a working table the first time a cron
fires, which can easily precede any inbound message.

Idempotent: `CREATE TABLE IF NOT EXISTS` plus an additive column check, so
running it twice adds nothing and a later store upgrade is not blocked. No data
is migrated from the dead `activity.db` — five-month-old dashboard rows about a
retired app are not conversation history, and the file is left untouched for
whoever wants to look at it.

Reversible only by deleting the DB, which is data loss — `down()` declines.
"""

from __future__ import annotations

DESCRIPTION = "Create ~/.aos/data/bridge.db for the bridge conversation store (aos#235)"

import sqlite3
from pathlib import Path

HOME = Path.home()
BRIDGE_DB = HOME / ".aos" / "data" / "bridge.db"

# The table first, the indexes last: a store written before `status` existed
# would fail `CREATE INDEX ... (status)` before the ALTER could add the column.
TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    direction     TEXT    NOT NULL CHECK (direction IN ('in', 'out')),
    chat_id       INTEGER,
    topic         TEXT,
    kind          TEXT,
    text_redacted TEXT,
    meta_json     TEXT,
    status        TEXT    NOT NULL DEFAULT 'sent'
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (ts);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);
"""

REQUIRED_COLUMNS = {
    "id", "ts", "direction", "chat_id", "topic", "kind", "text_redacted",
    "meta_json", "status",
}


def _columns(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(messages)")}


def check() -> bool:
    """True when the store exists with every column the bridge writes."""
    if not BRIDGE_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(BRIDGE_DB))
    except sqlite3.Error:
        return False
    try:
        return REQUIRED_COLUMNS <= _columns(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def up() -> bool:
    try:
        BRIDGE_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(BRIDGE_DB))
    except (OSError, sqlite3.Error) as e:
        print(f"  ✗ Could not open the bridge store ({e})")
        return False
    try:
        existed = bool(_columns(conn))
        conn.executescript(TABLE_SQL)
        # An older store from a pre-release build may predate `status`.
        if "status" not in _columns(conn):
            conn.execute("ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'")
            print("  Added messages.status")
        conn.executescript(INDEX_SQL)
        conn.commit()
        still_missing = REQUIRED_COLUMNS - _columns(conn)
        if still_missing:
            print(f"  ✗ Store is missing columns after migrating: {sorted(still_missing)}")
            return False
        print("  Bridge conversation store already present"
              if existed else "  Created the bridge conversation store")
        return True
    except sqlite3.Error as e:
        conn.rollback()
        print(f"  ✗ Schema creation failed ({e})")
        return False
    finally:
        conn.close()


def down() -> bool:
    """Not offered — the only way back is deleting the operator's messages."""
    return False


if __name__ == "__main__":
    print("Migration 124 already applied" if check() else ("Done" if up() else "Failed"))
