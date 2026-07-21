"""Nudge lifecycle — surface, dismiss, act, expire (the writeback loop).

Phase 5, unit 4. 600+ people nudges (intelligence_queue) had been generated and
never shown — the "roach motel": they went in, nothing came out, no feedback ever
reached the generator. This closes the loop:

  * SURFACE  — digest.mark_surfaced() stamps surfaced_at when a nudge is shown.
  * DISMISS  — operator says "not useful": status='dismissed' + a surface_feedback
               row so the generator can learn (fewer of that kind).
  * ACT      — operator acted on it: status='acted' + surface_feedback row.
  * EXPIRE   — the GC: any pending nudge older than EXPIRE_AFTER_DAYS is marked
               'expired' so the queue can never silently accumulate forever.

surface_feedback is the existing people.db table the draft/graduation paths
already write to; we reuse it so all operator feedback lives in one place.

All writes are to people.db (small, fast) — never comms.db.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

EXPIRE_AFTER_DAYS = 30

_ACTION_DISMISSED = "dismissed"
_ACTION_ACTED = "acted"


def _connect(people_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(people_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _gen_fb_id() -> str:
    import random
    import string
    return "fb_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def list_nudges(people_db: Path = PEOPLE_DB, *, limit: int | None = None,
                live_only: bool = True) -> list[dict]:
    """List nudges. live_only uses the people-intel live reader (pending, in
    window). Otherwise returns all pending rows regardless of window."""
    if not people_db.exists():
        return []
    conn = _connect(people_db)
    try:
        if live_only:
            from core.engine.people.intel import nudges as intel
            return intel.list_live_nudges(conn, limit=limit)
        sql = ("SELECT iq.id, iq.person_id, iq.surface_type, iq.priority, "
               "iq.status, iq.surfaced_at, iq.content, p.canonical_name AS name "
               "FROM intelligence_queue iq LEFT JOIN people p ON p.id=iq.person_id "
               "WHERE iq.status='pending' ORDER BY iq.priority ASC, iq.created_at DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def _record_feedback(conn: sqlite3.Connection, nudge_row: sqlite3.Row,
                     action: str, session_id: str | None) -> None:
    """Insert a surface_feedback row so the generator learns. Best-effort:
    a missing surface_feedback table (old people.db) is not fatal."""
    now = int(time.time())
    try:
        conn.execute(
            "INSERT INTO surface_feedback "
            "(id, person_id, surface_type, surface_at, operator_action, "
            " action_at, original_content, final_content, session_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_gen_fb_id(), nudge_row["person_id"], nudge_row["surface_type"],
             nudge_row["surfaced_at"] or now, action, now,
             nudge_row["content"], None, session_id))
    except sqlite3.OperationalError:
        pass


def _transition(nudge_id: str, action: str, new_status: str,
                people_db: Path, session_id: str | None) -> bool:
    if not people_db.exists():
        return False
    conn = _connect(people_db)
    try:
        row = conn.execute(
            "SELECT id, person_id, surface_type, surfaced_at, content "
            "FROM intelligence_queue WHERE id=?", (nudge_id,)).fetchone()
        if row is None:
            return False
        now = int(time.time())
        cur = conn.execute(
            "UPDATE intelligence_queue SET status=?, surfaced_at=COALESCE(surfaced_at,?) "
            "WHERE id=? AND status IN ('pending')",
            (new_status, now, nudge_id))
        if cur.rowcount == 0:
            return False
        _record_feedback(conn, row, action, session_id)
        conn.commit()
        return True
    finally:
        conn.close()


def dismiss(nudge_id: str, people_db: Path = PEOPLE_DB,
            session_id: str | None = None) -> bool:
    """Operator dismissed a nudge — record feedback so the generator learns."""
    return _transition(nudge_id, _ACTION_DISMISSED, "dismissed", people_db, session_id)


def act(nudge_id: str, people_db: Path = PEOPLE_DB,
        session_id: str | None = None) -> bool:
    """Operator acted on a nudge — record feedback."""
    return _transition(nudge_id, _ACTION_ACTED, "acted", people_db, session_id)


def expire_stale(people_db: Path = PEOPLE_DB, *, after_days: int = EXPIRE_AFTER_DAYS,
                 now_ts: int | None = None) -> int:
    """GC: mark pending nudges older than `after_days` as 'expired'. Returns the
    number expired. This is the roach-motel fix — the queue can never grow
    without bound."""
    if not people_db.exists():
        return 0
    now_ts = now_ts or int(time.time())
    cutoff = now_ts - after_days * 86400
    conn = _connect(people_db)
    try:
        cur = conn.execute(
            "UPDATE intelligence_queue SET status='expired' "
            "WHERE status='pending' AND created_at IS NOT NULL AND created_at < ?",
            (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
