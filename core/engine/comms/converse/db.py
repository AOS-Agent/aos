"""Converse — typed CRUD layer over the three converse tables in comms.db.

Everything else imports this: the supervisor daemon (core/services/converse,
T3), the turn handler (converse/turn.py, T2b), the Qareen API (core/qareen/
api/converse.py, T2c), and the migration that creates the schema
(core/infra/migrations/100_converse_init.py) all go through db.connect() +
these functions rather than touching sqlite3 directly. See
~/.aos/tmp/sessions-build/PLAN.md §2 and §4 for the design this implements.

Connection convention (matches core/engine/people/db.py): connect() opens a
row-factory connection and lazily applies schema.sql (CREATE TABLE/INDEX IF
NOT EXISTS — safe to call on every open). Each public function below takes an
open connection as its first argument, so callers control the transaction
boundary explicitly with conn.commit() — this lets the supervisor compose
multi-step operations (e.g. claim_batch + later apply_turn_result) as one
transaction when it needs to, while single-call convenience wrappers exist
for the common case (open, act, commit, close).

Every operation that must be atomic per PLAN.md §4 (ingest_inbound's
insert+cursor-advance, claim_batch's single-flight claim, apply_turn_result's
multi-row state application) commits internally before returning, using its
own connection, so a caller that just wants "do the thing" never has to think
about transactions.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from . import models
except ImportError:  # loaded standalone (e.g. sys.path-inserted by a migration)
    import models  # type: ignore

DB_PATH = Path.home() / ".aos" / "data" / "comms.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open comms.db and lazily apply the converse schema.

    Idempotent: schema.sql is entirely CREATE TABLE/INDEX IF NOT EXISTS, so
    this is safe to call on every connect, from every process (the
    supervisor, the Qareen API, tests) — first one in creates the tables,
    everyone else is a no-op check.
    """
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "conversation_sessions" not in tables:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()

    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_ts() -> int:
    return int(time.time())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"  # 12 hex chars, per PLAN.md §2


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


# ---------------------------------------------------------------------------
# conversation_sessions
# ---------------------------------------------------------------------------

def create_session(
    *,
    mode: str,
    voice: str,
    channel: str,
    conversation_ref: str,
    counterpart_handle: str,
    mission: str,
    person_id: str | None = None,
    person_name: str | None = None,
    success_criteria: str | None = None,
    constraints: str | None = None,
    tools: str = models.TOOLS_NONE,
    trust_level: int = 2,
    cursor: str | None = None,
    state_summary: str | None = None,
    artifacts: Any = None,
    max_messages: int = 30,
    expires_at: int | None = None,
    origin: str | None = None,
    status: str = models.STATUS_ACTIVE,
    db_path: Path | str | None = None,
) -> models.ConversationSession:
    """Create a new conversation session. Returns the created row.

    Validates the enum-shaped fields against models.py rather than trusting
    the caller — this is the single write path new sessions go through
    (CLI, Qareen POST /sessions, the Sentinel trigger factory in Phase D).
    """
    if mode not in models.MODES:
        raise ValueError(f"invalid mode: {mode!r}")
    if voice not in models.VOICES:
        raise ValueError(f"invalid voice: {voice!r}")
    if channel not in models.CHANNELS:
        raise ValueError(f"invalid channel: {channel!r}")
    if tools not in models.TOOLS_PROFILES:
        raise ValueError(f"invalid tools profile: {tools!r}")
    if status not in models.SESSION_STATUSES:
        raise ValueError(f"invalid status: {status!r}")

    sid = _new_id("cs")
    ts = now_ts()
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO conversation_sessions (
                id, mode, voice, channel, conversation_ref, counterpart_handle,
                person_id, person_name, mission, success_criteria, constraints,
                tools, trust_level, status, paused_reason, cursor, state_summary,
                artifacts, handling_started_at, turn_count, sent_count, error_count,
                max_messages, expires_at, origin, created_at, updated_at,
                closed_at, close_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL,
                      0, 0, 0, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                sid, mode, voice, channel, conversation_ref, counterpart_handle,
                person_id, person_name, mission, success_criteria, constraints,
                tools, trust_level, status, cursor, state_summary,
                _dumps(artifacts),
                max_messages, expires_at, origin, ts, ts,
            ),
        )
        conn.commit()
        return get_session(sid, db_path=db_path, _conn=conn)  # type: ignore[arg-type]
    finally:
        conn.close()


def get_session(
    session_id: str, *, db_path: Path | str | None = None, _conn: sqlite3.Connection | None = None
) -> models.ConversationSession | None:
    conn = _conn or connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return models.ConversationSession.from_row(row) if row else None
    finally:
        if _conn is None:
            conn.close()


def list_sessions(
    *,
    status: str | Iterable[str] | None = None,
    channel: str | None = None,
    mode: str | None = None,
    limit: int = 50,
    db_path: Path | str | None = None,
) -> list[models.ConversationSession]:
    """List sessions, most recently updated first.

    `status` accepts a single status, an iterable of statuses (e.g.
    models.ACTIVE_STATUSES), or None for all statuses.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if status is not None:
        statuses = [status] if isinstance(status, str) else list(status)
        clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    if channel is not None:
        clauses.append("channel = ?")
        params.append(channel)
    if mode is not None:
        clauses.append("mode = ?")
        params.append(mode)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM conversation_sessions {where} "
            f"ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [models.ConversationSession.from_row(r) for r in rows]
    finally:
        conn.close()


def set_status(
    session_id: str,
    status: str,
    *,
    paused_reason: str | None = None,
    close_reason: str | None = None,
    clear_paused_reason: bool = False,
    db_path: Path | str | None = None,
) -> models.ConversationSession | None:
    """Transition a session's status. Sets closed_at when moving to a
    terminal status; clears handling_started_at when leaving 'handling'.

    `clear_paused_reason=True` explicitly nulls paused_reason (e.g. on
    resume) — otherwise paused_reason is left untouched unless a new one is
    given, so a caller moving escalated -> handling doesn't need to know
    escalated's paused_reason value to preserve it correctly (it just won't
    pass one, and it stays).
    """
    if status not in models.SESSION_STATUSES:
        raise ValueError(f"invalid status: {status!r}")

    ts = now_ts()
    sets = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, ts]

    if paused_reason is not None:
        sets.append("paused_reason = ?")
        params.append(paused_reason)
    elif clear_paused_reason:
        sets.append("paused_reason = NULL")

    if status != models.STATUS_HANDLING:
        sets.append("handling_started_at = NULL")

    if status in models.TERMINAL_STATUSES:
        sets.append("closed_at = ?")
        params.append(ts)
        sets.append("close_reason = ?")
        params.append(close_reason or status)

    conn = connect(db_path)
    try:
        params.append(session_id)
        conn.execute(
            f"UPDATE conversation_sessions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        return get_session(session_id, db_path=db_path, _conn=conn)  # type: ignore[arg-type]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# session_messages — ingestion (inbound) and low-level insert
# ---------------------------------------------------------------------------

def add_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    role: str,
    direction: str,
    text: str,
    state: str,
    channel_message_id: str | None = None,
    ts: str | None = None,
    attempt_count: int = 0,
    error: str | None = None,
) -> str:
    """Low-level insert on an OPEN connection (caller commits). Returns the
    new message id. Used internally by ingest_inbound/apply_turn_result and
    exposed for callers that need to compose an insert into a larger
    transaction (e.g. the Qareen inject endpoint writing one operator row).
    """
    if role not in models.ROLES:
        raise ValueError(f"invalid role: {role!r}")
    if direction not in models.DIRECTIONS:
        raise ValueError(f"invalid direction: {direction!r}")
    if state not in models.MESSAGE_STATES:
        raise ValueError(f"invalid state: {state!r}")

    mid = _new_id("sm")
    conn.execute(
        """
        INSERT INTO session_messages (
            id, session_id, channel_message_id, role, direction, text, state,
            attempt_count, error, ts, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mid, session_id, channel_message_id, role, direction, text, state,
            attempt_count, error, ts or now_iso(), now_ts(),
        ),
    )
    return mid


def ingest_inbound(
    session_id: str,
    msgs: Iterable[Any],
    new_cursor: str | None,
    *,
    db_path: Path | str | None = None,
) -> list[models.SessionMessage]:
    """Durably record inbound messages and advance the cursor, in ONE
    transaction — the reliability fix from PLAN.md §2: a message is durable
    (state='received') before the cursor moves, and duplicate polls are
    no-ops via UNIQUE(session_id, channel_message_id).

    `msgs` is any iterable of objects/mappings exposing
    `channel_message_id`, `text`, `ts` (matches converse/channels.py's
    InboundMsg dataclass from T2a, but this function doesn't import that
    module — it just needs those three fields, via attribute or key access,
    to stay decoupled from the channel layer).

    Returns only the messages that were actually newly inserted (duplicates
    from a re-poll are silently skipped, not returned).
    """
    def _field(m: Any, name: str) -> Any:
        return getattr(m, name) if hasattr(m, name) else m[name]

    conn = connect(db_path)
    try:
        inserted: list[models.SessionMessage] = []
        for m in msgs:
            channel_message_id = _field(m, "channel_message_id")
            text = _field(m, "text")
            ts = _field(m, "ts")
            mid = _new_id("sm")
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO session_messages (
                    id, session_id, channel_message_id, role, direction, text,
                    state, attempt_count, error, ts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (
                    mid, session_id, channel_message_id, models.ROLE_CONTACT,
                    models.DIRECTION_INBOUND, text, models.MSG_RECEIVED, ts, now_ts(),
                ),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT * FROM session_messages WHERE id = ?", (mid,)
                ).fetchone()
                inserted.append(models.SessionMessage.from_row(row))
            # rowcount == 0 -> UNIQUE(session_id, channel_message_id) hit,
            # i.e. this message was already ingested by a prior poll. No-op.

        if new_cursor is not None:
            conn.execute(
                "UPDATE conversation_sessions SET cursor = ?, updated_at = ? WHERE id = ?",
                (new_cursor, now_ts(), session_id),
            )
        conn.commit()
        return inserted
    finally:
        conn.close()


def list_messages(
    session_id: str,
    *,
    limit: int = 100,
    after_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[models.SessionMessage]:
    """Transcript window, oldest first. `after_id` gives incremental reads
    (Qareen's GET /sessions/{id}/messages?after_id=) by filtering to rows
    created at or after that message's created_at (ties broken by id)."""
    conn = connect(db_path)
    try:
        if after_id:
            anchor = conn.execute(
                "SELECT created_at FROM session_messages WHERE id = ?", (after_id,)
            ).fetchone()
            if anchor is None:
                raise ValueError(f"unknown after_id: {after_id!r}")
            rows = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND (created_at > ? OR (created_at = ? AND id > ?))
                ORDER BY created_at ASC, id ASC LIMIT ?
                """,
                (session_id, anchor["created_at"], anchor["created_at"], after_id, limit),
            ).fetchall()
        else:
            # Last `limit` messages, returned oldest-first for transcript rendering.
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM session_messages WHERE session_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                ) ORDER BY created_at ASC, id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [models.SessionMessage.from_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handling — single-flight claim + deterministic turn-result application
# ---------------------------------------------------------------------------

def claim_batch(
    session_id: str, *, db_path: Path | str | None = None
) -> list[models.SessionMessage] | None:
    """Atomically claim all 'received' inbound messages for a session as one
    batch for handling (PLAN.md §4 step 4 HANDLE): marks them 'handling',
    bumps attempt_count, and sets session.status='handling' +
    handling_started_at. Single-flight guard: refuses (returns None) if the
    session is already 'handling' — the caller (the supervisor's dispatch
    loop) must not spawn a second handler for a session with one in flight.

    Returns None if there is nothing to claim, or if the session is already
    handling. Otherwise returns the claimed messages (oldest first).
    """
    conn = connect(db_path)
    try:
        session = conn.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise ValueError(f"unknown session: {session_id!r}")
        if session["status"] == models.STATUS_HANDLING:
            return None

        pending = conn.execute(
            "SELECT id FROM session_messages WHERE session_id = ? AND state = ? "
            "ORDER BY created_at ASC",
            (session_id, models.MSG_RECEIVED),
        ).fetchall()
        if not pending:
            return None

        ids = [r["id"] for r in pending]
        conn.execute(
            f"UPDATE session_messages SET state = ?, attempt_count = attempt_count + 1 "
            f"WHERE id IN ({','.join('?' for _ in ids)})",
            (models.MSG_HANDLING, *ids),
        )
        ts = now_ts()
        conn.execute(
            "UPDATE conversation_sessions SET status = ?, handling_started_at = ?, "
            "updated_at = ? WHERE id = ?",
            (models.STATUS_HANDLING, ts, ts, session_id),
        )
        conn.commit()

        rows = conn.execute(
            f"SELECT * FROM session_messages WHERE id IN ({','.join('?' for _ in ids)}) "
            f"ORDER BY created_at ASC",
            ids,
        ).fetchall()
        return [models.SessionMessage.from_row(r) for r in rows]
    finally:
        conn.close()


def apply_turn_result(
    session_id: str,
    *,
    action: str,
    message: str | None = None,
    state_summary: str | None = None,
    propose_actions: list[dict[str, Any]] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Deterministic DB-only application of a handler turn's output
    (PLAN.md §5/§4 step 5 APPLY). This function does NOT send anything and
    does NOT run the safety/confidence gate — those are T2b/T3 concerns
    (converse/gate.py wraps this with the hard-floor + trust-level checks
    before a 'reply' message is allowed to reach the channel's send()).
    What it guarantees, atomically:

      - every session_message currently 'handling' for this session -> 'handled'
      - state_summary (if given) replaces the stored one (the compaction strategy)
      - action='reply'/'complete' with a message -> a NEW outbound
        session_messages row, state='queued' (the caller sends it and then
        calls mark_message_sent/mark_message_send_failed — never sent here)
      - session.status set per action: reply/complete-with-more-to-do -> active,
        wait -> waiting, complete -> complete (closed), escalate -> escalated
      - session.turn_count += 1
      - propose_actions -> session_actions rows, status='proposed'

    Returns {"session": ConversationSession, "message_id": str|None,
             "action_ids": list[str]} for the caller (the supervisor) to act
    on next (i.e. actually call channel.send() for message_id if present).
    """
    if action not in models.TURN_ACTIONS:
        raise ValueError(f"invalid turn action: {action!r}")

    conn = connect(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise ValueError(f"unknown session: {session_id!r}")

        # 1. Close out the claimed batch.
        conn.execute(
            "UPDATE session_messages SET state = ? WHERE session_id = ? AND state = ?",
            (models.MSG_HANDLED, session_id, models.MSG_HANDLING),
        )

        # 2. New outbound row, if the handler produced a message to send.
        message_id: str | None = None
        if message:
            message_id = add_message(
                conn,
                session_id=session_id,
                role=models.ROLE_AGENT,
                direction=models.DIRECTION_OUTBOUND,
                text=message,
                state=models.MSG_QUEUED,
            )

        # 3. Status transition per action.
        ts = now_ts()
        sets = ["updated_at = ?", "turn_count = turn_count + 1", "handling_started_at = NULL"]
        params: list[Any] = [ts]

        if action == models.TURN_REPLY:
            sets.append("status = ?")
            params.append(models.STATUS_ACTIVE)
        elif action == models.TURN_WAIT:
            sets.append("status = ?")
            params.append(models.STATUS_WAITING)
        elif action == models.TURN_COMPLETE:
            sets += ["status = ?", "closed_at = ?", "close_reason = ?"]
            params += [models.STATUS_COMPLETE, ts, models.STATUS_COMPLETE]
        elif action == models.TURN_ESCALATE:
            sets += ["status = ?", "paused_reason = ?"]
            params += [models.STATUS_ESCALATED, models.PAUSED_REASON_ESCALATED]

        if state_summary is not None:
            sets.append("state_summary = ?")
            params.append(state_summary)

        params.append(session_id)
        conn.execute(
            f"UPDATE conversation_sessions SET {', '.join(sets)} WHERE id = ?", params
        )

        # 4. Proposed actions (human_touchpoint etc. — send_reply proposals
        #    from a held/gated reply are inserted by converse/gate.py, T2b,
        #    not here; this only handles what the handler itself proposed).
        action_ids: list[str] = []
        for pa in (propose_actions or []):
            kind = pa.get("kind")
            if kind not in models.ACTION_KINDS:
                raise ValueError(f"invalid propose_actions kind: {kind!r}")
            aid = _new_id("sa")
            payload = {k: v for k, v in pa.items() if k != "kind"}
            conn.execute(
                """
                INSERT INTO session_actions (
                    id, session_id, kind, payload, gate_reasons, status,
                    created_at, decided_at, executed_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, NULL)
                """,
                (aid, session_id, kind, _dumps(payload), models.ACTION_PROPOSED, now_ts()),
            )
            action_ids.append(aid)

        conn.commit()

        row = conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return {
            "session": models.ConversationSession.from_row(row),
            "message_id": message_id,
            "action_ids": action_ids,
        }
    finally:
        conn.close()


def mark_message_sent(
    message_id: str, *, channel_message_id: str | None = None, db_path: Path | str | None = None
) -> None:
    """Mark a 'queued' outbound message as sent, and bump the owning
    session's sent_count. Called by the supervisor immediately after a
    successful channel.send() — never here, so a row spends real time as
    visibly 'queued' between insert and send, which is the crash-safety
    property PLAN.md §4 calls out (a crash mid-send leaves a 'queued' row
    for the startup sweep to flag, not a silent double-send)."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT session_id FROM session_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown message: {message_id!r}")
        conn.execute(
            "UPDATE session_messages SET state = ?, channel_message_id = COALESCE(?, channel_message_id) "
            "WHERE id = ?",
            (models.MSG_SENT, channel_message_id, message_id),
        )
        conn.execute(
            "UPDATE conversation_sessions SET sent_count = sent_count + 1, updated_at = ? WHERE id = ?",
            (now_ts(), row["session_id"]),
        )
        conn.commit()
    finally:
        conn.close()


def mark_message_send_failed(
    message_id: str, error: str, *, db_path: Path | str | None = None
) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE session_messages SET state = ?, error = ? WHERE id = ?",
            (models.MSG_SEND_FAILED, error, message_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# session_actions — propose / decide / execute
# ---------------------------------------------------------------------------

def propose_action(
    session_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    gate_reasons: list[str] | None = None,
    db_path: Path | str | None = None,
) -> models.SessionAction:
    """Insert a pending approval (e.g. a gated 'reply' held by
    converse/gate.py, or a human_touchpoint the handler asked for)."""
    if kind not in models.ACTION_KINDS:
        raise ValueError(f"invalid kind: {kind!r}")

    aid = _new_id("sa")
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO session_actions (
                id, session_id, kind, payload, gate_reasons, status,
                created_at, decided_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (aid, session_id, kind, _dumps(payload), _dumps(gate_reasons),
             models.ACTION_PROPOSED, now_ts()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM session_actions WHERE id = ?", (aid,)).fetchone()
        return models.SessionAction.from_row(row)
    finally:
        conn.close()


def decide_action(
    action_id: str, decision: str, *, db_path: Path | str | None = None
) -> models.SessionAction:
    """Record an operator decision (approve/reject). Does NOT execute the
    action (e.g. does not send the reply) — that is the supervisor's job,
    which should call mark_action_executed after it actually acts on an
    approved action."""
    if decision not in (models.ACTION_APPROVED, models.ACTION_REJECTED):
        raise ValueError(f"invalid decision: {decision!r} (must be approved/rejected)")

    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE session_actions SET status = ?, decided_at = ? WHERE id = ?",
            (decision, now_ts(), action_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM session_actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown action: {action_id!r}")
        return models.SessionAction.from_row(row)
    finally:
        conn.close()


def mark_action_executed(action_id: str, *, db_path: Path | str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE session_actions SET status = ?, executed_at = ? WHERE id = ?",
            (models.ACTION_EXECUTED, now_ts(), action_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_pending_actions(
    *, limit: int = 50, db_path: Path | str | None = None
) -> list[models.SessionAction]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM session_actions WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (models.ACTION_PROPOSED, limit),
        ).fetchall()
        return [models.SessionAction.from_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Crash recovery — startup sweep
# ---------------------------------------------------------------------------

def sweep_stale(
    *,
    handling_timeout_s: int = 1200,
    action_expiry_h: int = 48,
    db_path: Path | str | None = None,
) -> dict[str, int]:
    """Startup crash sweep (PLAN.md §4 "startup:"): resets sessions stuck in
    'handling' past a stale handling_started_at back to 'active' and their
    claimed 'handling' messages back to 'received' (attempt_count is left
    intact — it was already bumped at claim time; retry backoff bookkeeping
    on top of this is the supervisor's job, not this function's). Also
    expires session_actions stuck 'proposed' for longer than action_expiry_h.

    Idempotent and safe to call on every daemon start (or periodically) —
    a session with no stale handling_started_at is left untouched.
    """
    now = now_ts()
    conn = connect(db_path)
    try:
        stale_sessions = conn.execute(
            "SELECT id FROM conversation_sessions WHERE status = ? AND handling_started_at IS NOT NULL "
            "AND handling_started_at < ?",
            (models.STATUS_HANDLING, now - handling_timeout_s),
        ).fetchall()
        sessions_reset = 0
        messages_reset = 0
        for row in stale_sessions:
            sid = row["id"]
            cur = conn.execute(
                "UPDATE session_messages SET state = ? WHERE session_id = ? AND state = ?",
                (models.MSG_RECEIVED, sid, models.MSG_HANDLING),
            )
            messages_reset += cur.rowcount
            conn.execute(
                "UPDATE conversation_sessions SET status = ?, handling_started_at = NULL, "
                "updated_at = ? WHERE id = ?",
                (models.STATUS_ACTIVE, now, sid),
            )
            sessions_reset += 1

        cur = conn.execute(
            "UPDATE session_actions SET status = ?, decided_at = ? "
            "WHERE status = ? AND created_at < ?",
            (models.ACTION_EXPIRED, now, models.ACTION_PROPOSED, now - action_expiry_h * 3600),
        )
        actions_expired = cur.rowcount

        conn.commit()
        return {
            "sessions_reset": sessions_reset,
            "messages_reset": messages_reset,
            "actions_expired": actions_expired,
        }
    finally:
        conn.close()
