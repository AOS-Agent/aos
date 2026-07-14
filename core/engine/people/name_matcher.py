"""Universal person name matcher.

Used by every adapter that needs to resolve a name string (from Photos,
WhatsApp, Contacts, etc.) to a person_id. Matches against all known
representations of a person's name:

  1. canonical_name (exact, case-insensitive)
  2. display_name
  3. nickname
  4. All aliases (from aliases table — includes "baba", "dad", "goose", etc.)
  5. first_name (if unique — prevents "Ahmed" matching 3 people)
  6. Phonetic variants (normalize.py PHONETIC_GROUPS — "idris" ↔ "idrees")

The matcher is precomputed: call ``build_name_index(conn)`` once at the
start of an adapter run, then call ``match(name, index)`` per candidate.
This is O(1) per match, not O(people).

Convention: ambiguous matches (name maps to 2+ person_ids) return None.
The adapter should skip rather than guess.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .normalize import PHONETIC_GROUPS


# Reverse lookup: variant → canonical phonetic form
_PHONETIC_REVERSE: dict[str, str] = {}
for _canon, _variants in PHONETIC_GROUPS.items():
    for _v in _variants:
        _PHONETIC_REVERSE[_v] = _canon


def _phonetic_key(name: str) -> str:
    """Convert a name to its phonetic canonical form (lowercase, space-joined)."""
    words = name.lower().split()
    return " ".join(_PHONETIC_REVERSE.get(w, w) for w in words)


class NameIndex:
    """Precomputed lookup structure for person name matching.

    Internal structure: for every lowered name variant, store a SET of
    person_ids. Ambiguous names (set size > 1) are skipped on lookup.
    """

    def __init__(self) -> None:
        self._exact: dict[str, set[str]] = {}  # lowered string → set of pids

    def _add(self, key: str, pid: str) -> None:
        if not key:
            return
        self._exact.setdefault(key, set()).add(pid)

    def add_person(
        self,
        pid: str,
        canonical_name: str | None,
        display_name: str | None,
        nickname: str | None,
        first_name: str | None,
        last_name: str | None,
        aliases: list[str] | None = None,
    ) -> None:
        # Exact forms
        for name in (canonical_name, display_name, nickname):
            if name:
                low = name.strip().lower()
                self._add(low, pid)
                # Also index the phonetic form
                phon = _phonetic_key(low)
                if phon != low:
                    self._add(phon, pid)

        # First name (short form)
        if first_name:
            low = first_name.strip().lower()
            self._add(low, pid)
            phon = _phonetic_key(low)
            if phon != low:
                self._add(phon, pid)

        # Aliases
        if aliases:
            for alias in aliases:
                if alias:
                    self._add(alias.strip().lower(), pid)

    def match(self, name: str) -> str | None:
        """Return person_id if ``name`` unambiguously resolves, else None.

        Tries (in order):
          1. Exact lowered match (most common case)
          2. Phonetic canonical of the input

        Returns None if the name maps to 0 or 2+ people.
        """
        if not name:
            return None
        low = name.strip().lower()

        candidates = self._exact.get(low)
        if candidates and len(candidates) == 1:
            return next(iter(candidates))

        # Try phonetic form
        phon = _phonetic_key(low)
        if phon != low:
            candidates = self._exact.get(phon)
            if candidates and len(candidates) == 1:
                return next(iter(candidates))

        return None

    def match_any(self, *names: str) -> str | None:
        """Try multiple name variants and return the first unambiguous match."""
        for name in names:
            pid = self.match(name)
            if pid is not None:
                return pid
        return None


def build_name_index(conn: sqlite3.Connection) -> NameIndex:
    """Build a NameIndex from people.db (people + aliases tables).

    Call once at the start of an extraction pass, then use
    ``index.match(name)`` or ``index.match_any(full, first, nick)``
    per candidate.
    """
    idx = NameIndex()

    # People table
    rows = conn.execute(
        "SELECT id, canonical_name, display_name, nickname, first_name, last_name "
        "FROM people WHERE is_archived = 0"
    ).fetchall()
    pid_aliases: dict[str, list[str]] = {}

    # Aliases table
    alias_rows = conn.execute("SELECT alias, person_id FROM aliases").fetchall()
    for r in alias_rows:
        pid_aliases.setdefault(r[1], []).append(r[0])

    for r in rows:
        idx.add_person(
            pid=r[0],
            canonical_name=r[1],
            display_name=r[2],
            nickname=r[3],
            first_name=r[4],
            last_name=r[5],
            aliases=pid_aliases.get(r[0]),
        )

    return idx
