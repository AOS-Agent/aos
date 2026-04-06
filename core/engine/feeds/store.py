"""store — Write intelligence briefs to qareen.db."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".aos" / "data" / "qareen.db"


def store_item(db_path: str | Path | None, item: dict) -> bool:
    """Insert an intelligence_brief into qareen.db.

    Expected item keys:
        source_id, title, url, summary, content, author, platform,
        layer, category, relevance_score, relevance_tags,
        published_at, project_id, raw_data, metadata

    Returns True if the item was inserted (new), False if it was a
    duplicate (URL already exists in the database).
    """
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db))
    try:
        # Check for duplicate by URL
        url = item.get("url", "")
        if url:
            existing = conn.execute(
                "SELECT 1 FROM intelligence_briefs WHERE url = ?",
                (url,),
            ).fetchone()
            if existing:
                return False

        brief_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        # Serialize JSON fields
        relevance_tags = item.get("relevance_tags", [])
        if isinstance(relevance_tags, list):
            relevance_tags = json.dumps(relevance_tags)

        raw_data = item.get("raw_data")
        if raw_data and not isinstance(raw_data, str):
            raw_data = json.dumps(raw_data)

        key_findings = item.get("key_findings")
        if key_findings and not isinstance(key_findings, str):
            key_findings = json.dumps(key_findings)

        conn.execute(
            """
            INSERT INTO intelligence_briefs (
                id, source_id, created_at, layer, category, platform,
                title, summary, content, url, author, raw_data,
                key_findings, relevance_score, relevance_tags,
                published_at, project_id, status, surfaced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unread', 0)
            """,
            (
                brief_id,
                item.get("source_id", ""),
                now,
                item.get("layer", 5),
                item.get("category", "news"),
                item.get("platform", ""),
                item.get("title", "Untitled"),
                item.get("summary", ""),
                item.get("content", ""),
                url or None,
                item.get("author", ""),
                raw_data,
                key_findings,
                item.get("relevance_score", 0.0),
                relevance_tags,
                item.get("published_at"),
                item.get("project_id"),
            ),
        )

        # Update source stats
        source_id = item.get("source_id")
        if source_id:
            conn.execute(
                """
                UPDATE intelligence_sources
                SET last_checked = ?,
                    last_success = ?,
                    items_total = items_total + 1,
                    consecutive_failures = 0
                WHERE id = ?
                """,
                (now, now, source_id),
            )

        conn.commit()
        return True

    except sqlite3.IntegrityError:
        # URL uniqueness constraint or other integrity error
        return False
    except sqlite3.OperationalError as e:
        print(f"[store] DB error: {e}")
        return False
    finally:
        conn.close()


def mark_source_checked(db_path: str | Path | None, source_id: str, success: bool = True):
    """Update a source's last_checked timestamp and failure count."""
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db))
    now = datetime.now(timezone.utc).isoformat()
    try:
        if success:
            conn.execute(
                """
                UPDATE intelligence_sources
                SET last_checked = ?, last_success = ?, consecutive_failures = 0
                WHERE id = ?
                """,
                (now, now, source_id),
            )
        else:
            conn.execute(
                """
                UPDATE intelligence_sources
                SET last_checked = ?, consecutive_failures = consecutive_failures + 1
                WHERE id = ?
                """,
                (now, source_id),
            )
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[store] Failed to update source {source_id}: {e}")
    finally:
        conn.close()


def url_exists(db_path: str | Path | None, url: str) -> bool:
    """Check if a URL already exists in intelligence_briefs."""
    if not url:
        return False
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT 1 FROM intelligence_briefs WHERE url = ?", (url,)
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
