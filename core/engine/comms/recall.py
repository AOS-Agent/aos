"""Recall — on-demand retrieval over the operator's own message history.

Phase 1 of Ambient Knowledge. A single query facade over comms.db (254K+
messages, FTS5) that lets an agent answer "I think I talked to this person
about this thing" and "what did we say about X" without the operator
re-explaining. Retrieval is verbatim (FTS5 keyword + person + timeframe); no
inference, no LLM — that is why every hit carries confidence 1.0.

THE CONTRACT (locked, dossier §recall). Every result row crossing this
interface is exactly:

    {
        "entity":      <the message payload — snippet-first for search,
                        full content for get()>,
        "confidence":  <float; 1.0 for verbatim retrieval>,
        "source_refs": [ {message_id, channel, date}, ... ]  # never empty
        "scope":       <str derived from the person's privacy_level>
    }

No field is ever omitted. See `_row()`.

ACCESS CONTROL LIVES HERE, not in the caller (locked decision #2). people.db
carries a `privacy_level` per contact — 1=full AI, 2=limited, 3=no AI
analysis. Recall ATTACHes people.db and filters in SQL: by default only
privacy_level 1 (full-AI) contacts are returned; anything more restricted
(>= PRIVATE_THRESHOLD) is excluded unless the caller passes the explicit,
operator-only `include_private=True`. Messages with no resolved person carry
no privacy signal and are scoped "unknown" — included by default (absence of a
person record is not a private flag), never silently dropped.

Usage (Python):
    from recall import RecallEngine
    eng = RecallEngine()
    rows = eng.search(query="ramadan", person="my mom", limit=20)
    row  = eng.get("im_12345")

The CLI wrapper is core/bin/cli/comms-recall.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# ── Paths & bounds ────────────────────────────────────────────────────────

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
SNIPPET_LEN = 200

# Verbatim retrieval — an FTS/SQL hit is the literal stored text, so it is
# certain. Kept as a named constant so a future derived-summary layer (which
# will hedge below 1.0) has an obvious seam.
VERBATIM_CONFIDENCE = 1.0

# people.privacy_level >= this is private-by-default. 1=full AI (visible),
# 2=limited, 3=no AI analysis (both excluded unless include_private).
PRIVATE_THRESHOLD = 2

# privacy_level -> scope label attached to every row.
_SCOPE_BY_LEVEL = {1: "open", 2: "limited", 3: "private"}


def scope_for(privacy_level: Optional[int]) -> str:
    """Map a person's privacy_level to a recall scope label.

    None (no resolved person, hence no privacy signal) -> "unknown".
    Any level not in the known map falls back to "private" — fail closed.
    """
    if privacy_level is None:
        return "unknown"
    return _SCOPE_BY_LEVEL.get(privacy_level, "private")


def _row(entity: dict, source_refs: list[dict], scope: str,
         confidence: float = VERBATIM_CONFIDENCE) -> dict:
    """Build one contract row. All four fields, always."""
    return {
        "entity": entity,
        "confidence": confidence,
        "source_refs": source_refs,
        "scope": scope,
    }


# ── Resolver wiring (lazy, so tests can inject a stub) ─────────────────────

def _default_resolver() -> Callable[[str], dict]:
    """Return people/resolver.resolve_contact, loaded by explicit file path.

    The people package uses sibling imports (`from db import connect`), so its
    directory must be on sys.path for the module to load at all. We add it,
    then load resolver.py by path under a unique name to avoid colliding with
    the stale core/engine/comms/resolver.py duplicate.
    """
    people_dir = Path(__file__).resolve().parents[1] / "people"
    if str(people_dir) not in sys.path:
        sys.path.insert(0, str(people_dir))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "aos_people_resolver", people_dir / "resolver.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load resolver from {people_dir}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.resolve_contact


# ── FTS query sanitisation ────────────────────────────────────────────────

def _fts_query(raw: str) -> Optional[str]:
    """Turn free text into a safe FTS5 MATCH expression.

    Each whitespace token is stripped of FTS operator characters and wrapped
    in double quotes (so `"faisal"` is a literal term, never syntax), and the
    quoted terms are ANDed. Returns None if nothing queryable remains — the
    caller then applies no FTS filter rather than crashing on empty MATCH.
    """
    if not raw:
        return None
    terms: list[str] = []
    for tok in raw.split():
        # Keep only characters safe inside an FTS5 quoted string; a literal
        # double-quote inside is escaped by doubling.
        cleaned = tok.replace('"', '""').strip()
        # Drop tokens that are pure punctuation (nothing alphanumeric).
        if not any(c.isalnum() for c in tok):
            continue
        terms.append(f'"{cleaned}"')
    if not terms:
        return None
    return " AND ".join(terms)


# ── Engine ────────────────────────────────────────────────────────────────

class RecallEngine:
    """Query facade over comms.db + people.db with in-tool access control."""

    def __init__(
        self,
        comms_db: Path = COMMS_DB,
        people_db: Path = PEOPLE_DB,
        resolver: Optional[Callable[[str], dict]] = None,
    ):
        self.comms_db = Path(comms_db)
        self.people_db = Path(people_db)
        self._resolver = resolver  # None => lazily use the real one

    # -- resolution --------------------------------------------------------

    def resolve_person(self, reference: str) -> dict:
        """Resolve any handle/name/alias to a person via the 5-tier resolver.

        Returns the resolver's result dict ({person_id, resolved, ...}).
        """
        resolver = self._resolver or _default_resolver()
        return resolver(reference)

    # -- connection --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.comms_db.exists():
            raise FileNotFoundError(f"comms.db not found at {self.comms_db}")
        # Read-only: the recall path never mutates the message store.
        conn = sqlite3.connect(f"file:{self.comms_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if self.people_db.exists():
            conn.execute(
                "ATTACH DATABASE ? AS people",
                (f"file:{self.people_db}?mode=ro",),
            )
        return conn

    def _people_attached(self, conn: sqlite3.Connection) -> bool:
        return any(r[1] == "people" for r in conn.execute("PRAGMA database_list"))

    # -- public API --------------------------------------------------------

    def search(
        self,
        query: Optional[str] = None,
        person: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        include_private: bool = False,
    ) -> list[dict]:
        """Recall messages by topic/keywords, person, timeframe — combinable.

        Args:
            query:   free-text keywords -> FTS5 MATCH (verbatim).
            person:  any handle/name/alias -> 5-tier resolver -> person scope.
            since:   ISO date 'YYYY-MM-DD' lower bound (inclusive).
            until:   ISO date 'YYYY-MM-DD' upper bound (inclusive).
            channel: restrict to one channel (imessage/whatsapp/email/...).
            limit:   result cap, clamped to [1, MAX_LIMIT].
            include_private: operator-only. Include restricted contacts
                     (privacy_level >= PRIVATE_THRESHOLD).

        Returns a bounded list of contract rows, most recent first.
        Snippet-first: entity carries a truncated snippet; the full text is
        fetched by-ref via get(message_id) so context stays lean.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))

        person_id = None
        if person:
            res = self.resolve_person(person)
            person_id = res.get("person_id")
            if not person_id:
                # Named a person we cannot resolve -> honestly zero results,
                # never a silent unscoped dump of everyone.
                return []

        conn = self._connect()
        try:
            have_people = self._people_attached(conn)
            wheres: list[str] = []
            params: list[Any] = []

            fts = _fts_query(query) if query else None
            if fts is not None:
                base = (
                    "SELECT m.id, m.channel, m.direction, m.timestamp, "
                    "m.content, m.person_id, "
                    f"{'p.canonical_name' if have_people else 'NULL'} AS person_name, "
                    f"{'p.privacy_level' if have_people else 'NULL'} AS privacy_level "
                    "FROM messages_fts f "
                    "JOIN messages m ON m.rowid = f.rowid "
                )
                wheres.append("f.messages_fts MATCH ?")
                params.append(fts)
            else:
                base = (
                    "SELECT m.id, m.channel, m.direction, m.timestamp, "
                    "m.content, m.person_id, "
                    f"{'p.canonical_name' if have_people else 'NULL'} AS person_name, "
                    f"{'p.privacy_level' if have_people else 'NULL'} AS privacy_level "
                    "FROM messages m "
                )
            if have_people:
                base += "LEFT JOIN people.people p ON p.id = m.person_id "

            if person_id:
                wheres.append("m.person_id = ?")
                params.append(person_id)
            if since:
                wheres.append("substr(m.timestamp,1,10) >= ?")
                params.append(since)
            if until:
                wheres.append("substr(m.timestamp,1,10) <= ?")
                params.append(until)
            if channel:
                wheres.append("m.channel = ?")
                params.append(channel)

            # Access control in SQL. Private contacts (privacy_level >=
            # threshold) are excluded unless include_private. NULL privacy
            # (no resolved person / people.db absent) is allowed by default.
            if not include_private and have_people:
                wheres.append(
                    "(m.person_id IS NULL OR p.privacy_level IS NULL "
                    "OR p.privacy_level < ?)"
                )
                params.append(PRIVATE_THRESHOLD)

            sql = base
            if wheres:
                sql += "WHERE " + " AND ".join(wheres) + " "
            sql += "ORDER BY m.timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [self._to_contract(r, full=False) for r in rows]
        finally:
            conn.close()

    def get(self, message_id: str, include_private: bool = False) -> Optional[dict]:
        """Fetch one message by id, full content, as a contract row.

        Honours the same access control: a private message returns None
        (indistinguishable from not-found) unless include_private is set.
        """
        conn = self._connect()
        try:
            have_people = self._people_attached(conn)
            sql = (
                "SELECT m.id, m.channel, m.direction, m.timestamp, "
                "m.content, m.person_id, "
                f"{'p.canonical_name' if have_people else 'NULL'} AS person_name, "
                f"{'p.privacy_level' if have_people else 'NULL'} AS privacy_level "
                "FROM messages m "
            )
            if have_people:
                sql += "LEFT JOIN people.people p ON p.id = m.person_id "
            sql += "WHERE m.id = ?"
            row = conn.execute(sql, (message_id,)).fetchone()
            if row is None:
                return None
            pl = row["privacy_level"]
            if (not include_private and pl is not None
                    and pl >= PRIVATE_THRESHOLD):
                return None
            return self._to_contract(row, full=True)
        finally:
            conn.close()

    # -- row shaping -------------------------------------------------------

    def _to_contract(self, row: sqlite3.Row, full: bool) -> dict:
        content = row["content"] or ""
        scope = scope_for(row["privacy_level"])
        entity: dict = {
            "type": "message",
            "message_id": row["id"],
            "person_id": row["person_id"],
            "person_name": row["person_name"],
            "channel": row["channel"],
            "direction": row["direction"],
            "timestamp": row["timestamp"],
        }
        if full:
            entity["content"] = content
        else:
            snippet = content[:SNIPPET_LEN]
            entity["snippet"] = snippet
            entity["truncated"] = len(content) > SNIPPET_LEN
        source_refs = [{
            "message_id": row["id"],
            "channel": row["channel"],
            "date": row["timestamp"],
        }]
        return _row(entity, source_refs, scope)


# ── Module-level convenience (defaults to live DBs) ────────────────────────

def search(**kwargs) -> list[dict]:
    return RecallEngine().search(**kwargs)


def get(message_id: str, include_private: bool = False) -> Optional[dict]:
    return RecallEngine().get(message_id, include_private=include_private)
