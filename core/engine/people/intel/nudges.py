"""People Intelligence — nudge generators.

Pure functions over an open ``sqlite3.Connection``. They read from
``relationship_state``, ``contact_metadata``, ``person_classification``,
and ``people``, and produce ``Nudge`` records that the runner inserts
into ``intelligence_queue`` (de-dup via ``UNIQUE(person_id, surface_type,
surface_after)``).

Three generators ship in Phase 6:

  * ``gen_birthdays``  — yearless or full ``MM-DD`` matches in next N days
  * ``gen_drift``      — current gap >> historical avg for core/active
  * ``gen_reconnect``  — long silence with core/active/emerging tier

A fourth ``follow_up`` type is reserved (for the parallel comms.db work)
but no generator is shipped yet.

Privacy: nudge content includes the person's canonical_name. This data
lives only in the operator's ``~/.aos/data/people.db``. The module never
logs prompts or names at INFO level.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import string
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from random import choices
from typing import Any

logger = logging.getLogger(__name__)

# Surface types — must match downstream consumers (CLI, morning-context, hook).
SURFACE_BIRTHDAY = "birthday"
SURFACE_DRIFT = "drift"
SURFACE_RECONNECT = "reconnect"
SURFACE_FOLLOW_UP = "follow_up"

# Default lookahead window for birthday nudges (days).
BIRTHDAY_LOOKAHEAD_DAYS = 14

# Reconnect: long-silence threshold for core/active/emerging tier (days).
RECONNECT_THRESHOLD_DAYS = 60

# Drift: current_gap > DRIFT_MULTIPLIER * avg_days_between → fire
DRIFT_MULTIPLIER = 2.0

# How long a nudge stays "live" after surface_after.
DRIFT_TTL_DAYS = 7
RECONNECT_TTL_DAYS = 14

_ID_CHARS = string.ascii_lowercase + string.digits


def _gen_id() -> str:
    return "iq_" + "".join(choices(_ID_CHARS, k=8))


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Nudge dataclass
# ---------------------------------------------------------------------------


@dataclass
class Nudge:
    person_id: str
    surface_type: str
    surface_after: int          # unix ts
    expires_at: int | None
    content: str                # operator-facing one-liner
    context: dict[str, Any] = field(default_factory=dict)
    priority: int = 3           # 1=highest, 5=lowest

    def to_row(self) -> dict[str, Any]:
        return {
            "id": _gen_id(),
            "person_id": self.person_id,
            "surface_type": self.surface_type,
            "priority": self.priority,
            "surface_after": self.surface_after,
            "surfaced_at": None,
            "status": "pending",
            "content": self.content,
            "context_json": json.dumps(self.context, ensure_ascii=False),
            "created_at": _now(),
            "expires_at": self.expires_at,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _midnight_utc(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())


def _start_of_day(now_ts: int) -> int:
    """Round a unix timestamp down to the start of its UTC day.

    Used as the dedup key for drift / reconnect so multiple runs on the
    same day collapse to a single queue row per (person, type).
    """
    return now_ts - (now_ts % 86400)


def _days_until(target: date, today: date) -> int:
    return (target - today).days


def _next_birthday_date(month: int, day: int, today: date) -> date | None:
    """Compute the next occurrence of MM-DD on or after today.

    Returns None for invalid dates (e.g. Feb 29 on non-leap years is rolled
    forward to Mar 1, which we accept).
    """
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        # Feb 29 in a non-leap year — fall back to Mar 1
        try:
            candidate = date(today.year, 3, 1) if (month, day) == (2, 29) else None
        except ValueError:
            return None
    if candidate is None:
        return None
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            candidate = date(today.year + 1, 3, 1)
    return candidate


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def gen_birthdays(
    conn: sqlite3.Connection,
    today: date | None = None,
    lookahead_days: int = BIRTHDAY_LOOKAHEAD_DAYS,
) -> list[Nudge]:
    """Find upcoming birthdays within ``lookahead_days``.

    Birthdays may be stored as ``YYYY-MM-DD`` (real year) or ``0000-MM-DD``
    (yearless from contacts). The year is ignored.
    """
    if today is None:
        today = date.today()

    rows = conn.execute(
        """
        SELECT cm.person_id AS pid, cm.birthday AS birthday, p.canonical_name AS name
        FROM contact_metadata cm
        JOIN people p ON cm.person_id = p.id
        WHERE p.is_archived = 0
          AND COALESCE(p.lifecycle_state, 'active') NOT IN ('deceased', 'archived', 'merged', 'blocked')
          AND COALESCE(p.is_self, 0) = 0
          AND cm.birthday IS NOT NULL
          AND length(cm.birthday) >= 10
        """
    ).fetchall()

    out: list[Nudge] = []
    for row in rows:
        b: str = row["birthday"] if isinstance(row, sqlite3.Row) else row[1]
        pid: str = row["pid"] if isinstance(row, sqlite3.Row) else row[0]
        name: str = row["name"] if isinstance(row, sqlite3.Row) else row[2]
        try:
            month = int(b[5:7])
            day = int(b[8:10])
        except (ValueError, IndexError):
            continue
        next_bday = _next_birthday_date(month, day, today)
        if not next_bday:
            continue
        delta = _days_until(next_bday, today)
        if delta < 0 or delta > lookahead_days:
            continue

        if delta == 0:
            content = f"Birthday today: {name}"
        elif delta == 1:
            content = f"Birthday tomorrow: {name}"
        else:
            content = f"Birthday in {delta} days: {name}"

        # Surface from the night before through the day after.
        surface_after = _midnight_utc(next_bday - timedelta(days=1))
        expires_at = _midnight_utc(next_bday + timedelta(days=1))

        out.append(
            Nudge(
                person_id=pid,
                surface_type=SURFACE_BIRTHDAY,
                surface_after=surface_after,
                expires_at=expires_at,
                content=content,
                context={"birthday_md": f"{month:02d}-{day:02d}", "days_away": delta},
                priority=2,
            )
        )
    return out


def gen_drift(
    conn: sqlite3.Connection,
    now_ts: int | None = None,
    multiplier: float = DRIFT_MULTIPLIER,
) -> list[Nudge]:
    """Find core/active people whose current gap exceeds ``multiplier`` × avg."""
    if now_ts is None:
        now_ts = _now()
    surface_after = _start_of_day(now_ts)

    rows = conn.execute(
        """
        SELECT
            p.id AS pid,
            p.canonical_name AS name,
            pc.tier AS tier,
            rs.last_interaction_at AS last_at,
            rs.avg_days_between AS avg_days
        FROM people p
        JOIN person_classification pc ON pc.person_id = p.id
        JOIN relationship_state rs ON rs.person_id = p.id
        WHERE p.is_archived = 0
          AND COALESCE(p.lifecycle_state, 'active') NOT IN ('deceased', 'archived', 'merged', 'blocked')
          AND COALESCE(p.is_self, 0) = 0
          AND pc.tier IN ('core', 'active')
          AND rs.avg_days_between IS NOT NULL
          AND rs.avg_days_between > 0
          AND rs.last_interaction_at IS NOT NULL
        """
    ).fetchall()

    out: list[Nudge] = []
    for row in rows:
        last_at = row["last_at"]
        avg = row["avg_days"]
        if last_at is None or avg is None:
            continue
        days_since = (now_ts - int(last_at)) // 86400
        if days_since <= multiplier * float(avg):
            continue
        content = (
            f"Pattern fading with {row['name']} — {days_since}d gap "
            f"(typical: {round(float(avg))}d)"
        )
        out.append(
            Nudge(
                person_id=row["pid"],
                surface_type=SURFACE_DRIFT,
                surface_after=surface_after,
                expires_at=surface_after + DRIFT_TTL_DAYS * 86400,
                content=content,
                context={
                    "tier": row["tier"],
                    "days_since": int(days_since),
                    "avg_days": round(float(avg), 1),
                },
                priority=1 if row["tier"] == "core" else 2,
            )
        )
    return out


def gen_reconnect(
    conn: sqlite3.Connection,
    now_ts: int | None = None,
    threshold_days: int = RECONNECT_THRESHOLD_DAYS,
) -> list[Nudge]:
    """Long silence with someone in core/active/emerging tier.

    Skips persons who already have a drift nudge in the last 14 days
    (drift wins — more specific signal).
    """
    if now_ts is None:
        now_ts = _now()
    surface_after = _start_of_day(now_ts)

    rows = conn.execute(
        """
        SELECT
            p.id AS pid,
            p.canonical_name AS name,
            pc.tier AS tier,
            rs.last_interaction_at AS last_at
        FROM people p
        JOIN person_classification pc ON pc.person_id = p.id
        JOIN relationship_state rs ON rs.person_id = p.id
        WHERE p.is_archived = 0
          AND COALESCE(p.lifecycle_state, 'active') NOT IN ('deceased', 'archived', 'merged', 'blocked')
          AND COALESCE(p.is_self, 0) = 0
          AND pc.tier IN ('core', 'active', 'emerging')
          AND rs.last_interaction_at IS NOT NULL
        """
    ).fetchall()

    cutoff_drift = now_ts - 14 * 86400
    drift_recent: set[str] = {
        r[0]
        for r in conn.execute(
            "SELECT person_id FROM intelligence_queue "
            "WHERE surface_type = ? AND created_at >= ?",
            (SURFACE_DRIFT, cutoff_drift),
        ).fetchall()
    }

    out: list[Nudge] = []
    for row in rows:
        if row["pid"] in drift_recent:
            continue
        last_at = row["last_at"]
        if last_at is None:
            continue
        days_since = (now_ts - int(last_at)) // 86400
        if days_since < threshold_days:
            continue
        content = (
            f"Haven't talked to {row['name']} in {days_since}d — "
            f"consider reaching out"
        )
        out.append(
            Nudge(
                person_id=row["pid"],
                surface_type=SURFACE_RECONNECT,
                surface_after=surface_after,
                expires_at=surface_after + RECONNECT_TTL_DAYS * 86400,
                content=content,
                context={"tier": row["tier"], "days_since": int(days_since)},
                priority=2 if row["tier"] == "core" else 3,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _insert_nudges(conn: sqlite3.Connection, nudges: list[Nudge]) -> int:
    """INSERT OR IGNORE each nudge. Returns number actually inserted."""
    if not nudges:
        return 0
    inserted = 0
    for n in nudges:
        row = n.to_row()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO intelligence_queue
                (id, person_id, surface_type, priority, surface_after,
                 surfaced_at, status, content, context_json, created_at, expires_at)
            VALUES
                (:id, :person_id, :surface_type, :priority, :surface_after,
                 :surfaced_at, :status, :content, :context_json, :created_at, :expires_at)
            """,
            row,
        )
        if cursor.rowcount > 0:
            inserted += 1
    return inserted


def generate_all(conn: sqlite3.Connection) -> dict[str, int]:
    """Run every generator and persist new nudges. Returns counts per type."""
    counts: dict[str, int] = {}
    for surface_type, gen in (
        (SURFACE_BIRTHDAY, gen_birthdays),
        (SURFACE_DRIFT, gen_drift),
        (SURFACE_RECONNECT, gen_reconnect),
    ):
        try:
            nudges = gen(conn)
        except sqlite3.OperationalError as e:
            # Required table missing (e.g. relationship_state not populated yet)
            logger.warning("nudge generator %s skipped: %s", surface_type, e)
            counts[surface_type] = 0
            continue
        counts[surface_type] = _insert_nudges(conn, nudges)
    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# Read helpers (for CLI / morning-context)
# ---------------------------------------------------------------------------


def list_live_nudges(
    conn: sqlite3.Connection,
    now_ts: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return pending nudges that are currently live (surface_after ≤ now < expires_at).

    Sort: priority asc, surface_after asc.
    """
    if now_ts is None:
        now_ts = _now()
    sql = """
        SELECT
            iq.id, iq.person_id, iq.surface_type, iq.priority,
            iq.surface_after, iq.expires_at, iq.content, iq.context_json,
            iq.created_at, p.canonical_name AS name
        FROM intelligence_queue iq
        LEFT JOIN people p ON p.id = iq.person_id
        WHERE iq.status = 'pending'
          AND iq.surface_after <= ?
          AND (iq.expires_at IS NULL OR iq.expires_at > ?)
        ORDER BY iq.priority ASC, iq.surface_after ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (now_ts, now_ts)).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            ctx = json.loads(r["context_json"]) if r["context_json"] else {}
        except json.JSONDecodeError:
            ctx = {}
        out.append({
            "id": r["id"],
            "person_id": r["person_id"],
            "name": r["name"],
            "surface_type": r["surface_type"],
            "priority": r["priority"],
            "surface_after": r["surface_after"],
            "expires_at": r["expires_at"],
            "content": r["content"],
            "context": ctx,
            "created_at": r["created_at"],
        })
    return out


def mark_actioned(conn: sqlite3.Connection, nudge_id: str) -> bool:
    """Mark a nudge as 'acted'. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE intelligence_queue SET status = 'acted', surfaced_at = COALESCE(surfaced_at, ?) "
        "WHERE id = ? AND status = 'pending'",
        (_now(), nudge_id),
    )
    conn.commit()
    return cur.rowcount > 0
