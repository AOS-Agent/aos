"""Ambient digest — the bounded, read-only "what the agent already knows".

Phase 5, injection side. A single, tight (<=15 line) markdown block distilled
from the enriched entity store (comms.db message_entities) plus the people
nudge queue (people.db intelligence_queue), injected at SessionStart, into
bridge sessions, and available via `comms-ambient digest`.

It answers, without the operator re-explaining:
  * what he promised and hasn't closed   (outbound commitments >= surface)
  * what others promised him and owes     (inbound commitments >= surface)
  * open questions addressed to him        (inbound question_open, unanswered)
  * recent money movement                  (transactions, last 7 days)
  * the top pending people nudges          (intelligence_queue, live)

ACCESS CONTROL is reused verbatim from the recall contract (locked decision
#2): people.db is ATTACHed read-only and any contact with privacy_level >=
PRIVATE_THRESHOLD is excluded from every section unless include_private (an
explicit, operator-only override). Messages with no resolved person carry no
privacy signal and are included by default. Direction ("who was speaking") is
ground truth for owed-by vs owed-to — the model's free-text `who` field is too
noisy (650/998 null on live data), so we join each entity's first source
message and read its direction, exactly as the sentinel context builder does.

READ-ONLY, always. The backfill engine may hold write locks on comms.db while
this runs; every connection here is opened `mode=ro` with a busy timeout, and
nothing in this module ever writes to comms.db. The only writeback this module
performs is marking surfaced nudges in people.db (see mark_surfaced), a small,
fast UPDATE on a different database.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# Repo-root bootstrap so `core.engine.comms.*` resolves whether run as a module
# or loaded by path from a hook/CLI (mirrors enrich/engine.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.comms.recall import PRIVATE_THRESHOLD  # noqa: E402

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

# Surface threshold: only lift/show entities the model was confident about
# (locked two-tier: store >=0.60, surface >=0.80).
SURFACE_MIN = 0.80

# How far back an inbound question stays an "open loop" worth surfacing.
QUESTION_WINDOW_DAYS = 21
# Transactions summary window.
TX_WINDOW_DAYS = 7

# Per-section caps — the whole digest must stay <= MAX_DIGEST_LINES.
MAX_DIGEST_LINES = 15
_OWED_BY_SHOWN = 3
_OWED_TO_SHOWN = 2
_QUESTIONS_SHOWN = 2
_NUDGES_SHOWN = 3


def _connect(comms_db: Path, people_db: Path) -> sqlite3.Connection:
    """Read-only comms.db with people.db attached read-only. Never writes."""
    conn = sqlite3.connect(f"file:{comms_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    if people_db.exists():
        conn.execute("ATTACH DATABASE ? AS people", (f"file:{people_db}?mode=ro",))
    return conn


def _people_attached(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "people" for r in conn.execute("PRAGMA database_list"))


def _privacy_clause(have_people: bool, include_private: bool) -> str:
    """SQL fragment excluding restricted contacts (reused from recall's rule)."""
    if include_private or not have_people:
        return ""
    return (" AND (e.person_id IS NULL OR p.privacy_level IS NULL "
            f"OR p.privacy_level < {PRIVATE_THRESHOLD}) ")


def _base_select(have_people: bool) -> str:
    # First source message drives direction + timestamp; people join gives name
    # and the privacy signal. json_extract('$[0]') is the entity's primary
    # source (validated on live data; multi-source entities are rare).
    name = "p.canonical_name" if have_people else "NULL"
    return (
        "SELECT e.id, e.entity_type, e.value, e.fields_json, e.confidence, "
        "e.person_id, e.source_ids, m.direction AS direction, "
        f"m.timestamp AS ts, {name} AS person_name "
        "FROM message_entities e "
        "JOIN messages m ON m.id = json_extract(e.source_ids,'$[0]') "
        + ("LEFT JOIN people.people p ON p.id = e.person_id " if have_people else "")
    )


def _short(text: str | None, n: int = 60) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _fields(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["fields_json"] or "{}")
    except Exception:
        return {}


# ── data accessors (each returns rows, most-recent-first, bounded) ──────────

def owed_by_you(conn, *, surface_min=SURFACE_MIN, include_private=False,
                limit=_OWED_BY_SHOWN) -> list[dict]:
    """Operator's own open commitments — extracted from OUTBOUND messages."""
    have = _people_attached(conn)
    sql = (_base_select(have) +
           "WHERE e.entity_type='commitment' AND e.status='active' "
           "AND e.confidence >= ? AND m.direction='outbound' "
           + _privacy_clause(have, include_private) +
           "ORDER BY m.timestamp DESC")
    out = []
    for r in conn.execute(sql, (surface_min,)):
        f = _fields(r)
        out.append({"what": f.get("what") or r["value"], "due": f.get("due"),
                    "person": r["person_name"], "ts": r["ts"]})
    return out[:limit] if limit else out


def owed_to_you(conn, *, surface_min=SURFACE_MIN, include_private=False,
                limit=_OWED_TO_SHOWN) -> list[dict]:
    """Commitments others made to the operator — from INBOUND messages."""
    have = _people_attached(conn)
    sql = (_base_select(have) +
           "WHERE e.entity_type='commitment' AND e.status='active' "
           "AND e.confidence >= ? AND m.direction='inbound' "
           + _privacy_clause(have, include_private) +
           "ORDER BY m.timestamp DESC")
    out = []
    for r in conn.execute(sql, (surface_min,)):
        f = _fields(r)
        out.append({"what": f.get("what") or r["value"], "due": f.get("due"),
                    "person": r["person_name"], "ts": r["ts"]})
    return out[:limit] if limit else out


def unanswered_questions(conn, *, surface_min=SURFACE_MIN, include_private=False,
                         window_days=QUESTION_WINDOW_DAYS,
                         limit=_QUESTIONS_SHOWN) -> list[dict]:
    """Recent inbound questions with no operator reply to that person after them.

    "Unanswered" = no OUTBOUND message to the same person_id after the question
    timestamp. person_id NULL can't be checked, so it is included if recent.
    """
    have = _people_attached(conn)
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - window_days * 86400))
    unanswered = (
        " AND (e.person_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM messages r WHERE r.person_id = e.person_id "
        "AND r.direction='outbound' AND r.timestamp > m.timestamp)) ")
    sql = (_base_select(have) +
           "WHERE e.entity_type='question_open' AND e.status='active' "
           "AND e.confidence >= ? AND m.direction='inbound' "
           "AND substr(m.timestamp,1,10) >= ? "
           + _privacy_clause(have, include_private) + unanswered +
           "ORDER BY m.timestamp DESC")
    out = []
    for r in conn.execute(sql, (surface_min, since)):
        f = _fields(r)
        out.append({"q": f.get("value") or r["value"], "person": r["person_name"],
                    "ts": r["ts"]})
    return out[:limit] if limit else out


def recent_transactions(conn, *, include_private=False, days=TX_WINDOW_DAYS) -> dict:
    """Summary of transaction entities in the last `days` (count + merchants)."""
    have = _people_attached(conn)
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    sql = (_base_select(have) +
           "WHERE e.entity_type='transaction' AND e.status='active' "
           "AND substr(m.timestamp,1,10) >= ? "
           + _privacy_clause(have, include_private) +
           "ORDER BY m.timestamp DESC")
    merchants, count = [], 0
    for r in conn.execute(sql, (since,)):
        count += 1
        f = _fields(r)
        mrc = f.get("merchant")
        if mrc and mrc not in merchants:
            merchants.append(mrc)
    return {"count": count, "days": days, "merchants": merchants[:4]}


def top_nudges(people_db: Path = PEOPLE_DB, *, limit=_NUDGES_SHOWN) -> list[dict]:
    """Top live pending nudges by priority (reuses the people-intel reader)."""
    if not people_db.exists():
        return []
    try:
        from core.engine.people.intel import nudges as intel_nudges
    except Exception:
        return []
    conn = sqlite3.connect(f"file:{people_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        return intel_nudges.list_live_nudges(conn, limit=limit)
    except Exception:
        return []
    finally:
        conn.close()


# ── writeback: mark surfaced nudges (unit 4 feedback loop) ──────────────────

def mark_surfaced(nudge_ids: list[str], people_db: Path = PEOPLE_DB) -> int:
    """Stamp surfaced_at=now for pending nudges just shown. Idempotent.

    Small fast UPDATE on people.db (not comms.db). status stays 'pending' —
    surfaced_at records that the operator has seen it; dismiss/act move status.
    """
    if not nudge_ids or not people_db.exists():
        return 0
    now = int(time.time())
    conn = sqlite3.connect(str(people_db))
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        marked = 0
        for nid in nudge_ids:
            cur = conn.execute(
                "UPDATE intelligence_queue SET surfaced_at=? "
                "WHERE id=? AND surfaced_at IS NULL", (now, nid))
            marked += cur.rowcount
        conn.commit()
        return marked
    finally:
        conn.close()


# ── rendering ───────────────────────────────────────────────────────────────

def build_digest(comms_db: Path = COMMS_DB, people_db: Path = PEOPLE_DB, *,
                 include_private: bool = False, surface_nudges: bool = True) -> str:
    """Build the bounded ambient digest markdown (<= MAX_DIGEST_LINES lines).

    Returns "" if comms.db is absent or there is nothing worth surfacing — the
    caller then injects nothing (never an empty header). Never raises: any
    failure degrades to "" so a session/hook is never blocked.
    """
    try:
        if not comms_db.exists():
            return ""
        conn = _connect(comms_db, people_db)
    except Exception:
        return ""

    try:
        by = owed_by_you(conn, include_private=include_private)
        to = owed_to_you(conn, include_private=include_private)
        qs = unanswered_questions(conn, include_private=include_private)
        tx = recent_transactions(conn, include_private=include_private)
    except Exception:
        return ""
    finally:
        conn.close()

    nudges = top_nudges(people_db)

    lines: list[str] = []
    if by:
        head = "; ".join(_short(c["what"], 50)
                         + (f" (due {c['due']})" if c.get("due") else "")
                         for c in by)
        lines.append(f"- You owe ({len(by)}+): {head}")
    if to:
        head = "; ".join(
            (f"{_short(c['what'], 40)}" + (f" — {c['person']}" if c.get("person") else ""))
            for c in to)
        lines.append(f"- Owed to you: {head}")
    if qs:
        head = "; ".join(
            (f'"{_short(q["q"], 45)}"' + (f" ({q['person']})" if q.get("person") else ""))
            for q in qs)
        lines.append(f"- Unanswered: {head}")
    if tx and tx["count"]:
        mpart = (" — " + ", ".join(tx["merchants"])) if tx["merchants"] else ""
        lines.append(f"- Purchases ({tx['days']}d): {tx['count']} transactions{mpart}")
    if nudges:
        head = "; ".join(_short(n["content"], 55) for n in nudges)
        lines.append(f"- People: {head}")
        if surface_nudges:
            try:
                mark_surfaced([n["id"] for n in nudges], people_db)
            except Exception:
                pass

    if not lines:
        return ""

    # Header + body, hard-bounded.
    body = ["**Ambient (from your comms):**"] + lines
    return "\n".join(body[:MAX_DIGEST_LINES])


def bridge_prompt_block(comms_db: Path = COMMS_DB, people_db: Path = PEOPLE_DB) -> str:
    """A bridge-system-prompt-ready ambient block, or "" if nothing to surface.

    Best-effort and self-contained: any failure returns "" so a bridge session
    is never blocked. Used by the bridge session spawners so a Telegram session
    starts already aware of the operator's open loops.
    """
    try:
        digest = build_digest(comms_db, people_db, surface_nudges=True)
    except Exception:
        return ""
    if not digest:
        return ""
    return ("\n\nContext you already have about the operator's world "
            "(from their own comms — use it, don't re-ask):\n" + digest)


if __name__ == "__main__":
    print(build_digest(surface_nudges=False) or "(nothing to surface)")
