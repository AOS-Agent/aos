"""Qareen Audit Trail — Immutable log of every governed action.

Every action executed through the ActionRegistry gets an audit entry.
The audit trail is append-only and stored in SQLite at ~/.aos/data/qareen.db.

Usage:
    audit = AuditLog()
    await audit.initialize()

    entry = AuditEntry(
        actor="chief",
        action_name="create_task",
        params={"title": "Fix bug"},
        result="success",
    )
    await audit.log(entry)

    recent = await audit.query(actor="chief", limit=10)
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Audit entry
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    """A single audit record for one action execution.

    Attributes:
        id: Unique identifier for this entry (auto-generated UUID).
        timestamp: When the action was executed.
        actor: Who executed it — operator id, agent id, or "system".
        action_name: The registered action name (e.g. "create_task").
        params: The parameters passed to the action (sanitized — no secrets).
        result: Outcome: "success" or "failure".
        error: Error message if result == "failure", else None.
        duration_ms: How long the action took to execute in milliseconds.
        event_emitted: The event type that was emitted, if any.
        metadata: Additional context (e.g. session_id, project).
    """

    actor: str
    action_name: str
    params: dict[str, Any] = field(default_factory=dict)
    result: str = "success"  # "success" or "failure"
    error: str | None = None
    duration_ms: float = 0.0
    event_emitted: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Auto-populated
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)

    def is_success(self) -> bool:
        """Return True if this entry records a successful action."""
        return self.result == "success"

    def is_failure(self) -> bool:
        """Return True if this entry records a failed action."""
        return self.result == "failure"


# ---------------------------------------------------------------------------
# Audit log implementation
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = os.path.expanduser("~/.aos/data/qareen.db")


class AuditLog:
    """Persistent audit trail backed by SQLite.

    Storage: ~/.aos/data/qareen.db
    Table: audit_log

    The log is append-only. Entries are never modified or deleted.
    Query methods support filtering by actor, action, time range, and result.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize the audit log.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to ~/.aos/data/qareen.db.
        """
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._initialized = False
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def initialize(self) -> None:
        """Create the database and tables if they don't exist.

        Creates:
          - audit_log table with columns matching AuditEntry fields
          - Indexes on actor, action_name, timestamp
        """
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_name TEXT NOT NULL,
                params TEXT,
                result TEXT NOT NULL DEFAULT 'success',
                error TEXT,
                duration_ms REAL DEFAULT 0.0,
                event_emitted TEXT,
                metadata TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_log(result)"
        )
        conn.commit()
        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Lazy-initialize if not already done."""
        if not self._initialized:
            # Synchronous initialization for cases where we haven't awaited initialize()
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action_name TEXT NOT NULL,
                    params TEXT,
                    result TEXT NOT NULL DEFAULT 'success',
                    error TEXT,
                    duration_ms REAL DEFAULT 0.0,
                    event_emitted TEXT,
                    metadata TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_log(result)"
            )
            conn.commit()
            self._initialized = True

    def _sanitize_params(self, params: dict[str, Any]) -> str:
        """JSON-serialize params, redacting anything that looks like a secret."""
        sanitized = {}
        secret_keys = {"password", "secret", "token", "key", "api_key", "credential"}
        for k, v in params.items():
            if any(s in k.lower() for s in secret_keys):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = v
        try:
            return json.dumps(sanitized, default=str)
        except (TypeError, ValueError):
            return json.dumps({"_raw": str(params)})

    def _row_to_entry(self, row: sqlite3.Row) -> AuditEntry:
        """Convert a database row to an AuditEntry."""
        params_raw = row["params"]
        try:
            params = json.loads(params_raw) if params_raw else {}
        except (json.JSONDecodeError, TypeError):
            params = {}

        metadata_raw = row["metadata"]
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        ts_str = row["timestamp"]
        try:
            timestamp = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            timestamp = datetime.now()

        return AuditEntry(
            id=row["id"],
            timestamp=timestamp,
            actor=row["actor"],
            action_name=row["action_name"],
            params=params,
            result=row["result"],
            error=row["error"],
            duration_ms=row["duration_ms"] or 0.0,
            event_emitted=row["event_emitted"],
            metadata=metadata,
        )

    async def log(self, entry: AuditEntry) -> str:
        """Append an audit entry to the log.

        Args:
            entry: The AuditEntry to record.

        Returns:
            The entry's id.
        """
        self._ensure_initialized()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO audit_log
               (id, timestamp, actor, action_name, params, result,
                error, duration_ms, event_emitted, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.timestamp.isoformat(),
                entry.actor,
                entry.action_name,
                self._sanitize_params(entry.params),
                entry.result,
                entry.error,
                entry.duration_ms,
                entry.event_emitted,
                json.dumps(entry.metadata, default=str) if entry.metadata else None,
            ),
        )
        conn.commit()
        return entry.id

    async def query(
        self,
        *,
        actor: str | None = None,
        action_name: str | None = None,
        result: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters.

        All filter parameters are optional and combined with AND logic.

        Args:
            actor: Filter by actor identity.
            action_name: Filter by action name.
            result: Filter by result ("success" or "failure").
            since: Only entries after this timestamp.
            until: Only entries before this timestamp.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip (for pagination).

        Returns:
            List of matching AuditEntry objects, ordered by timestamp descending.
        """
        self._ensure_initialized()
        conn = self._get_conn()

        clauses: list[str] = []
        params: list[Any] = []

        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if action_name is not None:
            clauses.append("action_name = ?")
            params.append(action_name)
        if result is not None:
            clauses.append("result = ?")
            params.append(result)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat())

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            f"SELECT * FROM audit_log WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def count(
        self,
        *,
        actor: str | None = None,
        action_name: str | None = None,
        result: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Count audit entries matching the given filters.

        Args:
            actor: Filter by actor identity.
            action_name: Filter by action name.
            result: Filter by result ("success" or "failure").
            since: Only entries after this timestamp.
            until: Only entries before this timestamp.

        Returns:
            Number of matching entries.
        """
        self._ensure_initialized()
        conn = self._get_conn()

        clauses: list[str] = []
        params: list[Any] = []

        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if action_name is not None:
            clauses.append("action_name = ?")
            params.append(action_name)
        if result is not None:
            clauses.append("result = ?")
            params.append(result)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat())

        where = " AND ".join(clauses) if clauses else "1=1"
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM audit_log WHERE {where}", params
        ).fetchone()
        return row["cnt"] if row else 0

    async def get(self, entry_id: str) -> AuditEntry | None:
        """Retrieve a single audit entry by its ID.

        Args:
            entry_id: The UUID of the entry.

        Returns:
            The AuditEntry, or None if not found.
        """
        self._ensure_initialized()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM audit_log WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    async def recent(self, limit: int = 50) -> list[AuditEntry]:
        """Convenience method: return the most recent audit entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of AuditEntry objects, most recent first.
        """
        return await self.query(limit=limit)

    async def failures_since(self, since: datetime) -> list[AuditEntry]:
        """Convenience method: return all failures since a given time.

        Useful for health checks and alerting.

        Args:
            since: Only entries after this timestamp.

        Returns:
            List of failed AuditEntry objects, most recent first.
        """
        return await self.query(result="failure", since=since)

    async def actor_summary(
        self, actor: str, since: datetime | None = None
    ) -> dict[str, Any]:
        """Return a summary of an actor's actions.

        Args:
            actor: The actor identity to summarize.
            since: Only consider entries after this timestamp.

        Returns:
            Dict with keys: total_actions, successes, failures,
            actions_by_name (dict of action_name -> count),
            avg_duration_ms, last_action_at.
        """
        self._ensure_initialized()
        conn = self._get_conn()

        clauses = ["actor = ?"]
        params: list[Any] = [actor]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        where = " AND ".join(clauses)

        # Total counts
        row = conn.execute(
            f"SELECT COUNT(*) as total, "
            f"SUM(CASE WHEN result = 'success' THEN 1 ELSE 0 END) as successes, "
            f"SUM(CASE WHEN result = 'failure' THEN 1 ELSE 0 END) as failures, "
            f"AVG(duration_ms) as avg_duration, "
            f"MAX(timestamp) as last_action "
            f"FROM audit_log WHERE {where}",
            params,
        ).fetchone()

        # Actions by name
        name_rows = conn.execute(
            f"SELECT action_name, COUNT(*) as cnt "
            f"FROM audit_log WHERE {where} "
            f"GROUP BY action_name ORDER BY cnt DESC",
            params,
        ).fetchall()

        return {
            "total_actions": row["total"] if row else 0,
            "successes": row["successes"] if row else 0,
            "failures": row["failures"] if row else 0,
            "avg_duration_ms": round(row["avg_duration"] or 0, 1) if row else 0,
            "last_action_at": row["last_action"] if row else None,
            "actions_by_name": {r["action_name"]: r["cnt"] for r in name_rows},
        }
