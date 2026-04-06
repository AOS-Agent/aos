"""watchlist — Load active intelligence sources from qareen.db."""

import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path.home() / ".aos" / "data" / "qareen.db"


def load_sources(db_path: str | Path | None = None) -> list[dict]:
    """Load active intelligence_sources from qareen.db.

    Returns a list of source dicts with: id, name, platform, route,
    route_url, priority, keywords (parsed from JSON), is_active,
    layer, tier, update_cadence, url, category, project_id,
    last_checked, consecutive_failures, items_total.
    """
    db = Path(db_path) if db_path else DEFAULT_DB
    if not db.exists():
        print(f"[watchlist] Database not found: {db}")
        return []

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, name, platform, layer, tier, url, route, route_url,
                   priority, keywords, update_cadence, last_checked,
                   last_success, consecutive_failures, items_total,
                   config, is_active, category, project_id
            FROM intelligence_sources
            WHERE is_active = 1
            ORDER BY priority DESC, name ASC
            """
        ).fetchall()

        sources = []
        for row in rows:
            source = dict(row)
            # Parse keywords from JSON string to list
            raw_kw = source.get("keywords")
            if raw_kw:
                try:
                    source["keywords"] = json.loads(raw_kw)
                except (json.JSONDecodeError, TypeError):
                    source["keywords"] = []
            else:
                source["keywords"] = []

            # Parse config from JSON
            raw_cfg = source.get("config")
            if raw_cfg:
                try:
                    source["config"] = json.loads(raw_cfg)
                except (json.JSONDecodeError, TypeError):
                    source["config"] = {}
            else:
                source["config"] = {}

            sources.append(source)

        return sources
    except sqlite3.OperationalError as e:
        print(f"[watchlist] DB error: {e}")
        return []
    finally:
        conn.close()
