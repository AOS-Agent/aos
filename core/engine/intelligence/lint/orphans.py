"""Orphan detection — vault docs at stage 3+ with zero incoming backlinks.

Pure SQL, no LLM. Reads from vault_inventory (populated by the scanner).
Returns a list of (path, title, stage, type) tuples sorted by last_modified
so the operator sees the most recently modified orphans first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"


def find_orphans(db_path: Path | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return vault docs at stage 3+ with zero incoming backlinks."""
    db = db_path or DEFAULT_DB_PATH
    if not db.exists():
        return []

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            """
            SELECT path, title, stage, type, backlink_count, last_modified, word_count
            FROM vault_inventory
            WHERE is_orphan = 1
              AND stage >= 3
              AND type != 'index'
            ORDER BY last_modified DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
