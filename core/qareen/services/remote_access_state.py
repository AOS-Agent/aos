"""Remote Access state — single-row persistence for the Cloudflare remote-access feature.

Tracks the provisioning state of the Qareen Cloudflare tunnel + Access configuration
in qareen.db. There is exactly one logical row (id='singleton').

NO secrets are stored here: the Cloudflare API token and the cloudflared run-token
live ONLY in the macOS Keychain (via agent-secret). This table holds only
non-sensitive metadata + Cloudflare resource IDs.

status values: disconnected | provisioning | connected | error

The _conn() WAL pattern is copied verbatim from intelligence/session.py
(row_factory=sqlite3.Row, PRAGMA journal_mode=WAL, foreign_keys=ON). The table is
auto-created in __init__ via the same CREATE TABLE IF NOT EXISTS that lives in
schemas/qareen.sql, so the feature self-initializes even before migration 049 runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Single logical row identity
SINGLETON_ID = "singleton"

# Default state returned when no row exists yet
DEFAULT_STATE: dict[str, Any] = {"status": "disconnected"}

# JSON columns that need serialization/deserialization
JSON_COLUMNS = frozenset({"allowed_emails"})

# Columns that may be set via upsert()
UPDATABLE_COLUMNS = frozenset({
    "status", "hostname", "domain", "zone_id", "account_id",
    "tunnel_id", "dns_record_id", "access_app_id", "access_aud",
    "policy_id", "idp_id", "allowed_emails", "created_at",
    "updated_at", "error_message",
})

# Database location
AOS_DATA = Path.home() / ".aos"
DB_PATH = AOS_DATA / "data" / "qareen.db"


class RemoteAccessState:
    """SQLite-backed single-row remote-access state."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self._db_path = str(db_path)
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self) -> None:
        """Create the remote_access table if it doesn't exist (idempotent).

        Mirrors schemas/qareen.sql so the feature self-initializes even before
        migration 049 has run.
        """
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS remote_access (
                    id              TEXT PRIMARY KEY DEFAULT 'singleton',
                    status          TEXT NOT NULL DEFAULT 'disconnected',
                    hostname        TEXT,
                    domain          TEXT,
                    zone_id         TEXT,
                    account_id      TEXT,
                    tunnel_id       TEXT,
                    dns_record_id   TEXT,
                    access_app_id   TEXT,
                    access_aud      TEXT,
                    policy_id       TEXT,
                    idp_id          TEXT,
                    allowed_emails  TEXT,
                    created_at      TEXT,
                    updated_at      TEXT,
                    error_message   TEXT
                )
            """)
            conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a sqlite3.Row to a dict, parsing JSON columns."""
        d = dict(row)
        for key in JSON_COLUMNS:
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> dict[str, Any]:
        """Return the singleton state row as a dict.

        Returns a copy of {'status': 'disconnected'} when no row exists yet.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM remote_access WHERE id = ?", (SINGLETON_ID,)
            ).fetchone()
        if row is None:
            return dict(DEFAULT_STATE)
        return self._row_to_dict(row)

    def upsert(self, **fields: Any) -> dict[str, Any]:
        """Insert or update the singleton row. JSON fields are auto-serialized.

        Performs a partial update of only the provided columns (unset columns are
        left untouched on an existing row). Always stamps updated_at. On first
        write, created_at is stamped and status defaults to 'disconnected' unless
        supplied. Returns the resulting state dict.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Normalize the supplied fields, dropping unknown columns and
        # serializing JSON columns.
        clean: dict[str, Any] = {}
        for key, val in fields.items():
            if key not in UPDATABLE_COLUMNS:
                logger.warning("Ignoring unknown remote_access column: %s", key)
                continue
            if key in JSON_COLUMNS and not isinstance(val, str) and val is not None:
                clean[key] = json.dumps(val, ensure_ascii=False)
            else:
                clean[key] = val

        clean["updated_at"] = now

        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM remote_access WHERE id = ?", (SINGLETON_ID,)
            ).fetchone()

            if exists:
                set_parts = [f"{k} = ?" for k in clean]
                values: list[Any] = list(clean.values())
                values.append(SINGLETON_ID)
                conn.execute(
                    f"UPDATE remote_access SET {', '.join(set_parts)} WHERE id = ?",
                    values,
                )
            else:
                clean.setdefault("status", "disconnected")
                clean.setdefault("created_at", now)
                cols = ["id"] + list(clean.keys())
                placeholders = ", ".join("?" for _ in cols)
                insert_values: list[Any] = [SINGLETON_ID] + list(clean.values())
                conn.execute(
                    f"INSERT INTO remote_access ({', '.join(cols)}) "
                    f"VALUES ({placeholders})",
                    insert_values,
                )
            conn.commit()

        return self.get()

    def set_status(self, status: str, error: str | None = None) -> dict[str, Any]:
        """Convenience: update status and (optionally) the error_message."""
        return self.upsert(status=status, error_message=error)

    def clear(self) -> dict[str, Any]:
        """Reset to 'disconnected' and null all provisioning metadata."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO remote_access
                   (id, status, hostname, domain, zone_id, account_id,
                    tunnel_id, dns_record_id, access_app_id, access_aud,
                    policy_id, idp_id, allowed_emails, created_at,
                    updated_at, error_message)
                   VALUES (?, 'disconnected', NULL, NULL, NULL, NULL,
                           NULL, NULL, NULL, NULL,
                           NULL, NULL, NULL, NULL,
                           ?, NULL)""",
                (SINGLETON_ID, now),
            )
            conn.commit()
        return self.get()
