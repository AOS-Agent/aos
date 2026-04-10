"""Stale doc detection — stage 3+ docs not updated in N months.

Pure SQL, no LLM. Reads from vault_inventory. A stale doc is one that
was written into the vault at stage 3+ but hasn't been modified since
some threshold (default: 6 months). Stage-1 captures are NEVER flagged
as stale — they're meant to age.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
DEFAULT_STALE_MONTHS = 6


def find_stale(
    db_path: Path | None = None,
    months: int = DEFAULT_STALE_MONTHS,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return stage 3+ vault docs older than `months` since last modified."""
    db = db_path or DEFAULT_DB_PATH
    if not db.exists():
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            """
            SELECT path, title, stage, type, last_modified, word_count, backlink_count
            FROM vault_inventory
            WHERE stage >= 3
              AND type != 'index'
              AND last_modified < ?
            ORDER BY last_modified ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
