"""Companion Session Manager — persistence layer for intelligence sessions.

Manages companion session lifecycle in qareen.db. Each session tracks
transcript blocks, extracted notes, research cards, and AI processing state.

Session types:
  - conversation: Interactive session with real-time transcript + AI processing
  - processing: Background processing session (email triage, batch analysis)

Event logging supports SSE recovery: clients that reconnect can fetch
events they missed via sequence numbers.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Allowed session statuses and types
VALID_STATUSES = {"active", "paused", "ended"}
VALID_SESSION_TYPES = {"conversation", "processing"}

# JSON columns that need serialization/deserialization
JSON_COLUMNS = frozenset({
    "tags", "participants",
    "transcript_json", "notes_json", "research_json",
    "cards_json", "context_json", "summary_json",
})

# Columns that can be updated via update_session
UPDATABLE_COLUMNS = frozenset({
    "title", "session_type", "skill", "status",
    "paused_at", "ended_at",
    "tags", "participants",
    "transcript_json", "notes_json", "research_json",
    "cards_json", "context_json", "summary_json",
    "audio_path", "audio_duration_seconds",
    "last_processed_index", "utterance_count",
    "tasks_created", "decisions_locked",
    "approvals_total", "approvals_approved",
})

# Audio storage root
SESSIONS_DIR = Path.home() / ".aos" / "sessions"


class SessionManager:
    """SQLite-backed companion session persistence."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
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
        """Create / migrate tables on startup."""
        with self._conn() as conn:
            # -- sessions_v2 (upgraded from companion_sessions) --
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions_v2 (
                    id                    TEXT PRIMARY KEY,
                    title                 TEXT,
                    session_type          TEXT NOT NULL DEFAULT 'conversation',
                    skill                 TEXT,
                    status                TEXT NOT NULL DEFAULT 'active',
                    started_at            TEXT NOT NULL,
                    paused_at             TEXT,
                    ended_at              TEXT,

                    -- Metadata
                    tags                  TEXT NOT NULL DEFAULT '[]',
                    participants          TEXT NOT NULL DEFAULT '[]',

                    -- Content (JSON blobs)
                    transcript_json       TEXT NOT NULL DEFAULT '[]',
                    notes_json            TEXT NOT NULL DEFAULT '[]',
                    research_json         TEXT NOT NULL DEFAULT '[]',
                    cards_json            TEXT NOT NULL DEFAULT '[]',
                    context_json          TEXT NOT NULL DEFAULT '{}',
                    summary_json          TEXT NOT NULL DEFAULT '{}',

                    -- Audio
                    audio_path            TEXT,
                    audio_duration_seconds REAL,

                    -- Processing state
                    last_processed_index  INTEGER NOT NULL DEFAULT 0,
                    utterance_count       INTEGER NOT NULL DEFAULT 0,

                    -- Stats
                    tasks_created         INTEGER NOT NULL DEFAULT 0,
                    decisions_locked      INTEGER NOT NULL DEFAULT 0,
                    approvals_total       INTEGER NOT NULL DEFAULT 0,
                    approvals_approved    INTEGER NOT NULL DEFAULT 0
                )
            """)

            # -- session_events (kept from previous schema, with FK to sessions_v2) --
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   TEXT NOT NULL,
                    event_type   TEXT NOT NULL,
                    event_data   TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    sequence_num INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions_v2(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_events_seq
                ON session_events(session_id, sequence_num)
            """)

            # -- Migrate data from old companion_sessions if it exists --
            self._migrate_from_v1(conn)

            conn.commit()

    def _migrate_from_v1(self, conn: sqlite3.Connection) -> None:
        """One-time migration: copy rows from companion_sessions -> sessions_v2."""
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='companion_sessions'"
            ).fetchone()
            if not row:
                return  # No old table, nothing to migrate

            # Check if already migrated (sessions_v2 has rows)
            count = conn.execute("SELECT COUNT(*) AS c FROM sessions_v2").fetchone()["c"]
            if count > 0:
                return  # Already have data, skip

            old_rows = conn.execute("SELECT * FROM companion_sessions").fetchall()
            if not old_rows:
                return

            for old in old_rows:
                old_d = dict(old)
                # Map old notes_json (dict) to new notes_json (list of groups)
                notes_raw = old_d.get("notes_json", "{}")
                try:
                    notes_dict = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
                except (json.JSONDecodeError, TypeError):
                    notes_dict = {}

                notes_groups: list[dict] = []
                if isinstance(notes_dict, dict):
                    for topic, items in notes_dict.items():
                        if isinstance(items, list):
                            notes_groups.append({
                                "id": uuid.uuid4().hex[:8],
                                "topic": topic,
                                "items": items,
                            })

                conn.execute(
                    """INSERT OR IGNORE INTO sessions_v2
                       (id, title, session_type, status, started_at, ended_at,
                        transcript_json, notes_json, research_json, cards_json,
                        context_json, last_processed_index, utterance_count)
                       VALUES (?, ?, 'conversation', ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        old_d["id"],
                        old_d.get("title"),
                        old_d.get("status", "ended"),
                        old_d.get("started_at", ""),
                        old_d.get("ended_at"),
                        old_d.get("transcript_json", "[]"),
                        json.dumps(notes_groups, ensure_ascii=False),
                        old_d.get("research_json", "[]"),
                        old_d.get("cards_json", "[]"),
                        old_d.get("context_json", "{}"),
                        old_d.get("last_processed_index", 0),
                        old_d.get("utterance_count", 0),
                    ),
                )

            # Migrate events too
            try:
                old_events = conn.execute(
                    "SELECT * FROM companion_session_events"
                ).fetchall()
                for ev in old_events:
                    ev_d = dict(ev)
                    conn.execute(
                        """INSERT OR IGNORE INTO session_events
                           (session_id, event_type, event_data, created_at, sequence_num)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            ev_d["session_id"],
                            ev_d["event_type"],
                            ev_d["event_data"],
                            ev_d["created_at"],
                            ev_d["sequence_num"],
                        ),
                    )
            except Exception:
                logger.debug("Could not migrate old session events", exc_info=True)

            logger.info("Migrated %d sessions from companion_sessions to sessions_v2", len(old_rows))

        except Exception:
            logger.debug("V1 migration check skipped", exc_info=True)

    # ------------------------------------------------------------------
    # Row serialization
    # ------------------------------------------------------------------

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
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        session_type: str = "conversation",
        skill: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a new session. Returns the full session dict."""
        if session_type not in VALID_SESSION_TYPES:
            session_type = "conversation"

        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        now = datetime.now(timezone.utc).isoformat()

        # Ensure audio directory exists
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        audio_path = str(session_dir / "audio.wav")

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions_v2
                   (id, title, session_type, skill, status, started_at, audio_path)
                   VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                (session_id, title, session_type, skill, now, audio_path),
            )
            conn.commit()

        logger.info(
            "Created session: %s (type=%s, skill=%s)",
            session_id, session_type, skill,
        )

        session = self.get_session(session_id)
        return session  # type: ignore[return-value]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a session by ID, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions_v2 WHERE id = ?", (session_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_active_session(self) -> dict[str, Any] | None:
        """Return the most recent active session, or None."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM sessions_v2
                   WHERE status = 'active'
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List sessions, newest first. Optionally filter by status."""
        if status and status not in VALID_STATUSES:
            status = None

        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM sessions_v2
                       WHERE status = ?
                       ORDER BY started_at DESC
                       LIMIT ? OFFSET ?""",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM sessions_v2
                       ORDER BY started_at DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def update_session(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update session fields. JSON fields are auto-serialized.

        Returns the updated session dict, or None if not found.
        """
        if not fields:
            return self.get_session(session_id)

        set_parts: list[str] = []
        values: list[Any] = []

        for key, val in fields.items():
            if key not in UPDATABLE_COLUMNS:
                logger.warning("Ignoring unknown session column: %s", key)
                continue
            set_parts.append(f"{key} = ?")
            if key in JSON_COLUMNS and not isinstance(val, str):
                values.append(json.dumps(val, ensure_ascii=False))
            else:
                values.append(val)

        if not set_parts:
            return self.get_session(session_id)

        values.append(session_id)
        sql = f"UPDATE sessions_v2 SET {', '.join(set_parts)} WHERE id = ?"

        with self._conn() as conn:
            conn.execute(sql, values)
            conn.commit()

        return self.get_session(session_id)

    def pause_session(self, session_id: str) -> dict[str, Any] | None:
        """Pause a session. Returns updated session or None."""
        session = self.get_session(session_id)
        if not session:
            return None
        if session["status"] != "active":
            logger.warning("Cannot pause session %s — status is %s", session_id, session["status"])
            return session

        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions_v2 SET status = 'paused', paused_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

        logger.info("Paused session: %s", session_id)
        return self.get_session(session_id)

    def resume_session(self, session_id: str) -> dict[str, Any] | None:
        """Resume a paused session. Returns updated session or None."""
        session = self.get_session(session_id)
        if not session:
            return None
        if session["status"] != "paused":
            logger.warning("Cannot resume session %s — status is %s", session_id, session["status"])
            return session

        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions_v2 SET status = 'active', paused_at = NULL WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        logger.info("Resumed session: %s", session_id)
        return self.get_session(session_id)

    def end_session(self, session_id: str) -> dict[str, Any] | None:
        """End a session — set status to 'ended' and record end time.

        Also generates a session summary from notes and cards.
        Returns the final session dict.
        """
        session = self.get_session(session_id)
        if not session:
            return None
        if session["status"] == "ended":
            return session

        now = datetime.now(timezone.utc).isoformat()
        summary = self._generate_summary(session)

        with self._conn() as conn:
            conn.execute(
                """UPDATE sessions_v2
                   SET status = 'ended', ended_at = ?, summary_json = ?
                   WHERE id = ?""",
                (now, json.dumps(summary, ensure_ascii=False), session_id),
            )
            conn.commit()

        logger.info("Ended session: %s", session_id)
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its events. Returns True if found."""
        with self._conn() as conn:
            conn.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))
            result = conn.execute("DELETE FROM sessions_v2 WHERE id = ?", (session_id,))
            conn.commit()
            deleted = result.rowcount > 0

        if deleted:
            logger.info("Deleted session: %s", session_id)
            # Clean up audio directory (non-blocking, best effort)
            session_dir = SESSIONS_DIR / session_id
            if session_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(session_dir)
                except Exception:
                    logger.debug("Could not clean up session dir: %s", session_dir)

        return deleted

    # ------------------------------------------------------------------
    # Auto-title generation
    # ------------------------------------------------------------------

    def auto_generate_title(self, session_id: str) -> str:
        """Generate a short title from the first transcript segments.

        Uses a simple heuristic: extract distinctive words from the first
        few transcript segments, ignoring stopwords.

        Returns the generated title (also saved to the session).
        """
        session = self.get_session(session_id)
        if not session:
            return ""

        transcript = session.get("transcript_json", [])
        if not transcript:
            return ""

        from .auto_title import generate_title_from_transcript
        title = generate_title_from_transcript(transcript)

        if title:
            self.update_session(session_id, title=title)

        return title

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def add_tag(self, session_id: str, tag: str) -> dict[str, Any] | None:
        """Add a tag to a session. Returns updated session."""
        session = self.get_session(session_id)
        if not session:
            return None

        tags: list[str] = session.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        tag = tag.strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
            return self.update_session(session_id, tags=tags)

        return session

    def remove_tag(self, session_id: str, tag: str) -> dict[str, Any] | None:
        """Remove a tag from a session. Returns updated session."""
        session = self.get_session(session_id)
        if not session:
            return None

        tags: list[str] = session.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        tag = tag.strip().lower()
        if tag in tags:
            tags.remove(tag)
            return self.update_session(session_id, tags=tags)

        return session

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def set_audio_path(self, session_id: str, path: str) -> dict[str, Any] | None:
        """Set the audio file path for a session."""
        return self.update_session(session_id, audio_path=path)

    # ------------------------------------------------------------------
    # Notes (structured note groups)
    # ------------------------------------------------------------------

    def add_note_group(
        self,
        session_id: str,
        group: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Add a note group to a session.

        A note group is: {"id": "...", "topic": "...", "items": [...]}
        If no id is provided, one is generated.
        Returns the updated session.
        """
        session = self.get_session(session_id)
        if not session:
            return None

        notes: list[dict] = session.get("notes_json", [])
        if not isinstance(notes, list):
            notes = []

        if "id" not in group:
            group["id"] = uuid.uuid4().hex[:8]
        if "items" not in group:
            group["items"] = []

        notes.append(group)
        return self.update_session(session_id, notes_json=notes)

    def update_note_group(
        self,
        session_id: str,
        group_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a specific note group by its id.

        Updates can include: topic, items (replace), or append_items (add to list).
        Returns the updated session.
        """
        session = self.get_session(session_id)
        if not session:
            return None

        notes: list[dict] = session.get("notes_json", [])
        if not isinstance(notes, list):
            return session

        found = False
        for group in notes:
            if group.get("id") == group_id:
                if "topic" in updates:
                    group["topic"] = updates["topic"]
                if "items" in updates:
                    group["items"] = updates["items"]
                if "append_items" in updates and isinstance(updates["append_items"], list):
                    existing = group.get("items", [])
                    existing.extend(updates["append_items"])
                    group["items"] = existing
                found = True
                break

        if not found:
            return session

        return self.update_session(session_id, notes_json=notes)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def increment_stat(
        self,
        session_id: str,
        stat_name: str,
    ) -> dict[str, Any] | None:
        """Increment a stat counter by 1. Returns updated session.

        Valid stat names: tasks_created, decisions_locked,
                          approvals_total, approvals_approved
        """
        valid_stats = {
            "tasks_created", "decisions_locked",
            "approvals_total", "approvals_approved",
        }
        if stat_name not in valid_stats:
            logger.warning("Unknown stat: %s", stat_name)
            return self.get_session(session_id)

        session = self.get_session(session_id)
        if not session:
            return None

        current = session.get(stat_name, 0)
        return self.update_session(session_id, **{stat_name: current + 1})

    # ------------------------------------------------------------------
    # Session events (SSE recovery)
    # ------------------------------------------------------------------

    def log_event(
        self,
        session_id: str,
        event_type: str,
        event_data: dict[str, Any] | str,
    ) -> int:
        """Log a session event. Returns the sequence number."""
        now = datetime.now(timezone.utc).isoformat()
        data_str = (
            event_data if isinstance(event_data, str)
            else json.dumps(event_data, ensure_ascii=False)
        )

        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(MAX(sequence_num), 0) + 1 AS next_seq
                   FROM session_events WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                """INSERT INTO session_events
                   (session_id, event_type, event_data, created_at, sequence_num)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, event_type, data_str, now, seq),
            )
            conn.commit()

        return seq

    def get_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        """Get events after a given sequence number (for SSE recovery)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM session_events
                   WHERE session_id = ? AND sequence_num > ?
                   ORDER BY sequence_num ASC""",
                (session_id, after_seq),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for r in rows:
            data = r["event_data"]
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                pass
            result.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "event_type": r["event_type"],
                "event_data": data,
                "created_at": r["created_at"],
                "sequence_num": r["sequence_num"],
            })
        return result

    # Backward-compat alias used by the existing companion.py
    def get_events_since(
        self,
        session_id: str,
        after_sequence: int,
    ) -> list[dict[str, Any]]:
        """Alias for get_events (backward compat)."""
        return self.get_events(session_id, after_seq=after_sequence)

    # ------------------------------------------------------------------
    # Summary generation
    # ------------------------------------------------------------------

    def _generate_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        """Generate a summary from notes, cards, and transcript.

        This is a structured extraction — no LLM call. Pulls together
        key points, tasks, ideas, decisions, and stats.
        """
        notes: list[dict] = session.get("notes_json", [])
        cards: list[dict] = session.get("cards_json", [])
        transcript: list[dict] = session.get("transcript_json", [])

        # Extract items by topic from notes groups
        key_points: list[str] = []
        tasks: list[str] = []
        ideas: list[str] = []
        decisions: list[str] = []

        if isinstance(notes, list):
            for group in notes:
                if not isinstance(group, dict):
                    continue
                topic = group.get("topic", "").lower()
                items = group.get("items", [])
                if "task" in topic:
                    tasks.extend(items)
                elif "idea" in topic:
                    ideas.extend(items)
                elif "decision" in topic:
                    decisions.extend(items)
                else:
                    key_points.extend(items)
        elif isinstance(notes, dict):
            # Legacy dict format
            for topic, items in notes.items():
                if not isinstance(items, list):
                    continue
                topic_lower = topic.lower()
                if "task" in topic_lower:
                    tasks.extend(items)
                elif "idea" in topic_lower:
                    ideas.extend(items)
                elif "decision" in topic_lower:
                    decisions.extend(items)
                else:
                    key_points.extend(items)

        # Count approved/dismissed cards
        approved_cards = [c for c in cards if isinstance(c, dict) and c.get("status") == "approved"]
        dismissed_cards = [c for c in cards if isinstance(c, dict) and c.get("status") == "dismissed"]

        # Duration
        started = session.get("started_at", "")
        duration_minutes: float | None = None
        if started:
            try:
                start_dt = datetime.fromisoformat(started)
                duration_minutes = round(
                    (datetime.now(timezone.utc) - start_dt).total_seconds() / 60, 1
                )
            except (ValueError, TypeError):
                pass

        return {
            "key_points": key_points,
            "tasks": tasks,
            "ideas": ideas,
            "decisions": decisions,
            "open_questions": [],  # populated by LLM later
            "stats": {
                "utterance_count": session.get("utterance_count", 0),
                "transcript_segments": len(transcript),
                "note_groups": len(notes) if isinstance(notes, list) else len(notes.keys()) if isinstance(notes, dict) else 0,
                "cards_approved": len(approved_cards),
                "cards_dismissed": len(dismissed_cards),
                "tasks_created": session.get("tasks_created", 0),
                "decisions_locked": session.get("decisions_locked", 0),
                "duration_minutes": duration_minutes,
            },
        }
