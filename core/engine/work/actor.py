"""
Attribution — every work mutation is signed.

The requirement (BRIEF-CONTRACT.md § "Attribution — every change is signed"):
if an agent completed a task, the agent signs off; if the operator changed it by
hand, it is attributed to the operator. No anonymous state changes.

**Never invent an actor.** When the environment gives no honest signal the
answer is ``Actor(kind="unknown")`` and the UI renders it as "unattributed".
The failure mode this whole module exists to kill is the opposite: silently
defaulting to "operator" and thereby *falsifying* who did the work.

Storage — the existing tables, no parallel structure
----------------------------------------------------
``entity_history`` already is the audit trail the contract describes: one row
per changed field, carrying ``actor``, ``actor_type`` and ``session_id``. It is
written from ``WorkAdapter._record_history``, the single choke point every
mutation flows through. Nothing new is invented here:

* ``created_by`` / ``started_by`` / ``completed_by`` are **derived** from
  entity_history (the row whose ``field_name='status'`` and ``new_value`` is
  ``'active'`` / ``'done'``; creation writes a ``field_name='created'`` row).
  They are not columns and not a JSON blob.
* the audit trail IS entity_history, read back oldest-first.
* ``tasks.modified_by`` / ``modified_at`` carry the most recent actor so a
  cheap list query can show attribution without joining history.

The actor string format matches the vocabulary already in the DB and in
``core/qareen/ontology/activity.py``: ``operator``, ``agent:chief``,
``system:work``, ``cron:nightly-review``, ``import:islah``, ``unknown``.

All writes here are best-effort: failing to record a signature must never lose
the mutation itself. A missing audit line is a gap; a lost completion is a bug.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# brief_types.py is the shared, dependency-free schema module (see the
# contract). Import it by path so this module works both as a package member
# and as a flat script (cli.py inserts this directory on sys.path).
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from brief_types import Actor  # type: ignore
except Exception:  # pragma: no cover - only if brief_types is missing entirely
    # Identical shape, so the work CLI keeps running even if the shared schema
    # module is absent. Never let attribution take down task management.
    @dataclass
    class Actor:  # type: ignore[no-redef]
        kind: str = "unknown"
        name: str = "unknown"
        session_id: str | None = None
        at: str = ""


__all__ = [
    "Actor",
    "ATTRIBUTION_FIX_AT",
    "AUDIT_CAP",
    "actor_from_dict",
    "actor_to_dict",
    "actor_type_for",
    "attribution_for",
    "describe",
    "display_name",
    "full_history_for",
    "history_for",
    "is_suspect_operator_row",
    "record_change",
    "record_created",
    "resolve_actor",
    "set_modified_by",
    "to_adapter_string",
]

# How many audit entries `work who` shows by default. The trail itself is
# unbounded in entity_history — capping is a display concern, not storage.
AUDIT_CAP = 20

# When the adapter stopped defaulting an unset actor to "operator".
#
# Every row written before this instant carries whatever the default was, so an
# `operator` row from before it is NOT evidence the operator did anything — it
# may be agent work wearing the human's name. Consumers should render pre-cutoff
# operator rows as unattributed rather than as fact.
#
# This is the canonical definition. cli.py (`work who`) and brief.py both key
# off it; it lives here so neither has to reach into the other's source.
ATTRIBUTION_FIX_AT = "2026-07-26T16:11:00"


def is_suspect_operator_row(actor_str: str, actor_type: str, timestamp: str) -> bool:
    """True when an `operator` attribution predates the fix and can't be trusted.

    A pre-cutoff *named* actor (`agent:chief`) is still trustworthy — only the
    silently-defaulted value is suspect.
    """
    return (
        (actor_str or "") == "operator"
        and (actor_type or "") == "operator"
        and str(timestamp or "") < ATTRIBUTION_FIX_AT
    )

# Known actor kinds (mirrors brief_types.ACTOR_KINDS).
KINDS = ("operator", "agent", "cron", "import", "unknown")

# Env vars an agent dispatch wrapper may use to name itself.
_AGENT_NAME_ENV = ("AOS_AGENT", "CLAUDE_AGENT", "CLAUDE_CODE_AGENT")

# Where a Claude Code session id actually lives. Verified against a live
# session: the real variable is CLAUDE_CODE_SESSION_ID. ``CLAUDE_SESSION_ID``
# — the name in the contract, and the one cli.py has been reading since before
# this layer existed — is NOT set by Claude Code and never has been, so every
# lookup of it silently returned None. Both are checked so the code keeps
# working if the naming ever changes back.
_SESSION_ID_ENV = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
)


def session_id_from_env() -> str | None:
    """The current Claude Code session id, or None outside a session."""
    for key in _SESSION_ID_ENV:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _in_claude_code() -> bool:
    """True inside a Claude Code session.

    ``CLAUDECODE=1`` is set for every session and every Bash tool subprocess it
    spawns, which makes it a zero-configuration signal that an agent — not a
    human at a shell — is driving. This is what lets an ordinary chat session
    sign its own work without any hook, wrapper or env plumbing.
    """
    return (os.environ.get("CLAUDECODE") or "").strip() == "1"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Resolution ──────────────────────────────────────────────────────────

def _parse_spec(spec: str, *, session_id: str | None = None) -> Actor | None:
    """Parse an actor spec string into an Actor, or None if it says nothing.

    Accepted forms:
      ``operator``            → the human, by hand
      ``chief`` / ``advisor`` → an agent by name (bare names are agents)
      ``agent:chief``         → explicit kind:name
      ``system:work``         → the system acting on its own (a cascade)
      ``cron:nightly-review`` → a scheduled job
      ``import:islah``        → a bulk import
      ``unknown``             → explicitly unattributed
    """
    spec = (spec or "").strip()
    if not spec:
        return None

    kind: str | None = None
    name = spec
    if ":" in spec:
        head, _, tail = spec.partition(":")
        head = head.strip().lower()
        if head == "system" and tail.strip():
            # The system acting on its own behalf (cascades, migrations). Not
            # an operator and not a named agent — recorded as an automated
            # actor so it can never read as a human decision.
            return Actor(kind="cron", name=f"system:{tail.strip()}",
                         session_id=session_id, at=_now())
        if head in KINDS and tail.strip():
            kind, name = head, tail.strip()

    if kind is None:
        low = spec.lower()
        if low == "operator":
            kind, name = "operator", "operator"
        elif low in ("cli", "user"):
            # The vocabulary already in the DB treats these as the human.
            kind, name = "operator", "operator"
        elif low in KINDS:
            kind, name = low, low
        else:
            # A bare name that isn't the operator is an agent. This is the
            # caller's claim, not a guess by us.
            kind, name = "agent", spec

    if kind == "unknown":
        return Actor(kind="unknown", name="unknown", at=_now())
    return Actor(kind=kind, name=name, session_id=session_id, at=_now())


def _agent_name_from_env() -> str:
    for key in _AGENT_NAME_ENV:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return "claude"


def resolve_actor(explicit: str | None = None) -> Actor:
    """Resolve who is making this change, in the contract's strict order.

    1. Explicit ``--actor`` flag / API ``actor`` field.
    2. Env var ``AOS_ACTOR`` (set by the runner's agent dispatch).
    3. A Claude Code session → an agent, named from the env or "claude".
       This is the case that makes an ordinary chat session sign its own work
       instead of it silently landing on the operator.
    4. Interactive TTY with no session → the operator, by hand.
    5. Otherwise → ``unknown``. Never invent one.

    Step 4 comes AFTER step 3 deliberately: an agent's Bash subprocess can
    inherit a terminal, so testing for a TTY first would hand agent work back
    to the operator — the exact falsification this module exists to prevent.
    """
    session_id = session_id_from_env()

    # 1 — explicit wins over everything.
    if explicit:
        actor = _parse_spec(explicit, session_id=session_id)
        if actor is not None:
            return actor

    # 2 — dispatch wrappers announce themselves here.
    env_actor = (os.environ.get("AOS_ACTOR") or "").strip()
    if env_actor:
        actor = _parse_spec(env_actor, session_id=session_id)
        if actor is not None:
            return actor

    # 3 — a Claude Code session is running: an agent is driving. Either signal
    # is enough; the session id is preferred because it is traceable.
    if session_id or _in_claude_code():
        return Actor(kind="agent", name=_agent_name_from_env(),
                     session_id=session_id, at=_now())

    # 4 — a human typed this at a terminal.
    try:
        if sys.stdin.isatty():
            return Actor(kind="operator", name="operator", at=_now())
    except Exception:
        pass

    # 5 — no honest signal.
    return Actor(kind="unknown", name="unknown", at=_now())


# ── Serialization ───────────────────────────────────────────────────────

def actor_to_dict(a: Actor) -> dict:
    """JSON-safe dict for an Actor. ``None`` in → an unknown actor out."""
    if a is None:
        return {"kind": "unknown", "name": "unknown", "session_id": None, "at": ""}
    return {
        "kind": getattr(a, "kind", "unknown") or "unknown",
        "name": getattr(a, "name", "unknown") or "unknown",
        "session_id": getattr(a, "session_id", None),
        "at": getattr(a, "at", "") or "",
    }


def actor_from_dict(d: dict | None) -> Actor | None:
    """Rehydrate an Actor. ``None``/garbage in → ``None`` out (not a fake actor)."""
    if not d or not isinstance(d, dict):
        return None
    return Actor(
        kind=str(d.get("kind") or "unknown"),
        name=str(d.get("name") or "unknown"),
        session_id=d.get("session_id"),
        at=str(d.get("at") or ""),
    )


def to_adapter_string(a: Actor) -> str:
    """Render an Actor in the string vocabulary the DB and adapter already use.

    operator → ``operator``; an agent → ``agent:<name>``; cron/import → their
    prefixed forms; unknown → ``unknown``. Round-trips through ``_parse_spec``.
    """
    if a is None:
        return "unknown"
    kind = getattr(a, "kind", "unknown") or "unknown"
    name = (getattr(a, "name", "") or "").strip()
    if kind == "operator":
        return "operator"
    if kind == "unknown" or not name:
        return "unknown"
    if name.startswith(("agent:", "system:", "cron:", "import:")):
        return name
    return f"{kind}:{name}"


def actor_type_for(a: Actor) -> str:
    """The ``entity_history.actor_type`` value for this actor.

    Preserves the values already in the column (``operator``, ``agent``) and
    adds ``unknown`` rather than letting an unattributed change masquerade as
    either. cron/import classify as ``system``, matching activity.py's avatar
    vocabulary.
    """
    kind = getattr(a, "kind", "unknown") or "unknown"
    if kind == "operator":
        return "operator"
    if kind == "agent":
        return "agent"
    if kind == "unknown":
        return "unknown"
    return "system"


# ── Plain English ───────────────────────────────────────────────────────

def _display_name(actor: Actor) -> str:
    kind = getattr(actor, "kind", "unknown") or "unknown"
    name = (getattr(actor, "name", "") or "").strip()

    if kind == "operator":
        return "You"
    if kind == "unknown" or not name or name == "unknown":
        return "Someone"
    if name.startswith("system:"):
        return "The system"
    if kind == "cron":
        return f"The {name} cron" if name != "cron" else "A scheduled job"
    if kind == "import":
        return f"The {name} import" if name != "import" else "An import"
    if name.startswith("agent:"):
        name = name.split(":", 1)[1]
    if name == "unidentified":
        return "An unidentified agent"
    # agent — "chief" → "Chief", "session-close" → "Session-close"
    return name[:1].upper() + name[1:]


def display_name(actor: Actor) -> str:
    """Just the who, no verb: 'You' / 'Chief' / 'Someone'."""
    return _display_name(actor)


def describe(actor: Actor, verb: str, subject: str) -> str:
    """Plain English: 'Chief completed "Draft the constitution"'.

    Operator actor renders as 'You'. Unknown renders as 'Someone'.
    """
    who = _display_name(actor)
    verb = (verb or "changed").strip()
    subject = (subject or "").strip()
    if not subject:
        return f"{who} {verb}"
    return f'{who} {verb} "{subject}"'


# ── Storage (entity_history) ────────────────────────────────────────────

_conn: sqlite3.Connection | None = None
_conn_path: str | None = None


def _resolve_path() -> str:
    """Where the work DB is, as the CALLING module currently understands it.

    Not simply ``backend._resolve_db_path()``. That reads ``AOS_WORK_DB`` and
    otherwise falls through to the real ``~/.aos/data/work.db`` — but the
    module globals ``backend.DB_PATH`` / ``engine.DB_PATH`` are what the rest
    of the code actually opens, and test fixtures redirect those *without*
    setting the env var.

    Resolving independently is how a test suite came to write fabricated
    history rows onto three real operator tasks: the adapter used the scratch
    DB while attribution went to the live one. Honour the module global first.
    """
    for name in ("backend", "engine"):
        mod = sys.modules.get(name)
        path = getattr(mod, "DB_PATH", None) if mod is not None else None
        if path:
            return str(path)
    import backend as _backend  # local import — backend imports this module
    return str(_backend._resolve_db_path())


def _db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """A connection to the work DB.

    Callers that already hold one should pass it — that is exact, avoids a
    second connection, and makes it impossible for attribution to land in a
    different database from the mutation it describes.

    The fallback is cached *by resolved path*, not globally: the path can
    change between calls (every test gets its own throwaway DB), and a
    connection pinned to the first path it ever saw would silently write into
    the wrong database.
    """
    if conn is not None:
        return conn
    global _conn, _conn_path
    path = _resolve_path()
    if _conn is not None and _conn_path == path:
        return _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    new = sqlite3.connect(path)
    new.execute("PRAGMA journal_mode=WAL")
    new.execute("PRAGMA busy_timeout=5000")
    new.row_factory = sqlite3.Row
    _conn, _conn_path = new, path
    return _conn


def _write_history(task_id: str, field: str, old, new, actor: Actor,
                   conn: sqlite3.Connection | None = None) -> None:
    """Insert one entity_history row. Best-effort — never raises."""
    if not task_id:
        return
    try:
        conn = _db(conn)
        conn.execute(
            "INSERT INTO entity_history "
            "(entity_type, entity_id, field_name, old_value, new_value, "
            " actor, actor_type, timestamp, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "task", task_id, field,
                None if old is None else str(old),
                None if new is None else str(new),
                to_adapter_string(actor),
                actor_type_for(actor),
                getattr(actor, "at", "") or _now(),
                getattr(actor, "session_id", None),
            ),
        )
        conn.commit()
    except Exception:
        pass


def record_change(task_id: str, change: str, actor: Actor,
                  *, field: str | None = None, old=None, new=None,
                  conn: sqlite3.Connection | None = None) -> None:
    """Record one signed change in entity_history.

    The locked three-argument form takes a plain-English ``change`` phrase and
    files it under the pseudo-field ``change`` — for events that are not a
    single column edit (a handoff being written). Pass ``field``/``old``/``new``
    when the change IS a column edit, so the row is a real field diff like
    every other row in the table.
    """
    if field:
        _write_history(task_id, field, old, new, actor, conn)
    else:
        _write_history(task_id, "change", None, change, actor, conn)


def record_created(task_id: str, status: str, actor: Actor,
                   source: str | None = None,
                   conn: sqlite3.Connection | None = None) -> None:
    """Record task creation, so ``created_by`` can be derived like the rest.

    Creation is not a field diff, so it gets its own ``created`` row. This is
    what makes "who created this" answerable from the same table as everything
    else, instead of from ``tasks.created_by`` — which holds a source enum
    (``manual``/``subtask``/``islah-import``), not an actor.
    """
    _write_history(task_id, "created", source, status or "todo", actor, conn)


def set_modified_by(task_id: str, actor: Actor,
                    conn: sqlite3.Connection | None = None) -> None:
    """Stamp ``tasks.modified_by`` so a list query sees the last actor cheaply.

    entity_history stays the trail; this is the denormalised head of it. The
    column exists and was populated on only 72 of 1,927 rows — never by the UI
    path — so it disagreed with the history. This keeps them consistent.
    """
    if not task_id:
        return
    try:
        conn = _db(conn)
        conn.execute(
            "UPDATE tasks SET modified_by = ? WHERE id = ?",
            (to_adapter_string(actor), task_id),
        )
        conn.commit()
    except Exception:
        pass


# ── Reading ─────────────────────────────────────────────────────────────

def history_for(task_id: str, limit: int = 200,
                conn: sqlite3.Connection | None = None) -> list[dict]:
    """A task's entity_history, oldest first. ``[]`` if none or on error."""
    try:
        rows = _db(conn).execute(
            "SELECT field_name, old_value, new_value, actor, actor_type, "
            "       timestamp, session_id "
            "FROM entity_history "
            "WHERE entity_type = 'task' AND entity_id = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def _actor_from_row(row: dict) -> Actor:
    """Rebuild an Actor from an entity_history row, honestly.

    A row written before the attribution fix carries whatever the adapter
    defaulted to. We do not second-guess it here — the stored actor_type wins
    where it disagrees with the parsed name, because that column is what the
    writer actually asserted.
    """
    a = _parse_spec(row.get("actor") or "unknown",
                    session_id=row.get("session_id"))
    if a is None:
        a = Actor()
    a.at = row.get("timestamp") or ""
    stored_type = (row.get("actor_type") or "").strip()
    if stored_type == "operator" and a.kind != "operator":
        a.kind = "operator"
    elif stored_type == "unknown":
        a.kind = "unknown"
    return a


def _previous_id(task_id: str, conn: sqlite3.Connection | None = None) -> str | None:
    """The id this task carried before a move re-identified it, if any."""
    try:
        row = _db(conn).execute(
            "SELECT old_value FROM entity_history "
            "WHERE entity_type = 'task' AND entity_id = ? "
            "  AND field_name = 'moved_from' "
            "ORDER BY id ASC LIMIT 1",
            (task_id,),
        ).fetchone()
    except Exception:
        return None
    return row["old_value"] if row and row["old_value"] else None


def full_history_for(task_id: str, limit: int = 200,
                     conn: sqlite3.Connection | None = None) -> list[dict]:
    """A task's history including everything it accumulated under old ids.

    `move` re-IDs a task, and entity_history keys on entity_id — so without
    following the chain, moving a task silently orphans its whole past and it
    reads as unattributed. The `moved_from` row written at move time is the
    bridge; this walks it backwards.
    """
    seen: set[str] = set()
    chain: list[str] = []
    current: str | None = task_id
    while current and current not in seen and len(chain) < 10:
        seen.add(current)
        chain.append(current)
        current = _previous_id(current, conn)

    rows: list[dict] = []
    for tid in reversed(chain):          # oldest identity first
        rows.extend(history_for(tid, limit=limit, conn=conn))
    rows.sort(key=lambda r: r.get("timestamp") or "")
    return rows[-limit:]


def attribution_for(task_id: str,
                    conn: sqlite3.Connection | None = None) -> dict:
    """Derive who created / started / completed a task, plus the audit trail.

    Everything here comes out of entity_history, following the task across any
    re-IDs. Keys are absent — not faked — when the history does not record that
    event, which is the honest answer for the ~1,900 tasks that predate this
    layer.
    """
    rows = full_history_for(task_id, conn=conn)
    out: dict = {"audit": rows}
    for row in rows:
        field = row.get("field_name")
        new = row.get("new_value")
        if field == "created":
            out["created_by"] = actor_to_dict(_actor_from_row(row))
        elif field == "status":
            if new == "active":
                out["started_by"] = actor_to_dict(_actor_from_row(row))
            elif new == "done":
                out["completed_by"] = actor_to_dict(_actor_from_row(row))
    return out
