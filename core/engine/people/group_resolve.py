"""WhatsApp Group Member Resolution.

Resolves unresolved group_members (person_id IS NULL) to existing person
records by matching wa_jid and name against the people database.
"""

from __future__ import annotations

import re
import sqlite3
import string
import time
from pathlib import Path
from random import choices
from typing import Any

from rapidfuzz import fuzz

from .normalize import normalize_name, normalize_phone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

_ID_CHARS = string.ascii_lowercase + string.digits

# Pattern: wa_jid is like "971501234567@s.whatsapp.net" — extract digits
_WA_JID_DIGITS_RE = re.compile(r"^(\d+)@")

# Heuristic: a "good" pushname has a space and isn't just a phone number
_PHONE_ONLY_RE = re.compile(r"^[\d\s\+\-\(\)]+$")


def _gen_id(prefix: str) -> str:
    return prefix + "_" + "".join(choices(_ID_CHARS, k=8))


def _now() -> int:
    return int(time.time())


def _connect(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    if conn is not None:
        return conn
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


def _jid_to_phone(wa_jid: str | None) -> str | None:
    """Extract phone number from a WhatsApp JID.

    '971501234567@s.whatsapp.net' -> '+971501234567'
    LID format ('12345@lid') -> None (no phone in LID)
    """
    if not wa_jid:
        return None
    if wa_jid.endswith("@lid"):
        return None  # LIDs are opaque device IDs, not phone numbers
    m = _WA_JID_DIGITS_RE.match(wa_jid)
    if m:
        return "+" + m.group(1)
    return None


def _is_lid(wa_jid: str | None) -> bool:
    """Check if a JID is a Linked Device ID (not resolvable to phone)."""
    return bool(wa_jid and wa_jid.endswith("@lid"))


def _is_good_pushname(name: str | None) -> bool:
    """Check if a pushname is likely a real name (not just digits)."""
    if not name or len(name.strip()) < 2:
        return False
    # Must have at least a space (first + last name)
    if " " not in name.strip():
        return False
    # Must not be phone-only
    if _PHONE_ONLY_RE.match(name.strip()):
        return False
    return True


# ---------------------------------------------------------------------------
# GroupResolver
# ---------------------------------------------------------------------------


class GroupResolver:
    """Resolve unresolved WhatsApp group members to person records."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._conn = _connect(conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _find_by_phone(self, phone: str) -> str | None:
        """Find a person_id by normalized phone number across both identifier tables."""
        normalized = normalize_phone(phone)
        if not normalized:
            return None

        # Check contact_point first (may not exist if migration 027 not applied)
        if _has_table(self.conn, "contact_point"):
            row = self.conn.execute(
                "SELECT person_id FROM contact_point WHERE type = 'phone' AND normalized = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if row:
                return row["person_id"]

        # Fall back to person_identifiers
        row = self.conn.execute(
            "SELECT person_id FROM person_identifiers WHERE type = 'phone' AND normalized = ? LIMIT 1",
            (normalized,),
        ).fetchone()
        if row:
            return row["person_id"]

        # Also try the raw value with + prefix in case normalization varies
        row = self.conn.execute(
            "SELECT person_id FROM person_identifiers WHERE type = 'phone' AND value = ? LIMIT 1",
            (phone,),
        ).fetchone()
        if row:
            return row["person_id"]

        return None

    def _find_by_wa_jid(self, wa_jid: str) -> str | None:
        """Find a person_id by WhatsApp JID in person_identifiers."""
        row = self.conn.execute(
            "SELECT person_id FROM person_identifiers WHERE type = 'wa_jid' AND value = ? LIMIT 1",
            (wa_jid,),
        ).fetchone()
        if row:
            return row["person_id"]
        return None

    def _find_by_name(self, name: str) -> str | None:
        """Find a person_id by fuzzy matching canonical_name."""
        if not name or len(name.strip()) < 2:
            return None

        people = self.conn.execute(
            "SELECT id, canonical_name FROM people WHERE is_archived = 0 AND canonical_name IS NOT NULL"
        ).fetchall()

        best_score = 0
        best_id = None
        for person in people:
            score = fuzz.token_sort_ratio(name.strip(), person["canonical_name"])
            if score >= 88 and score > best_score:
                best_score = score
                best_id = person["id"]

        return best_id

    def _create_person(self, name: str, wa_jid: str | None) -> str:
        """Create a new person record from a group member's pushname."""
        now = _now()
        person_id = _gen_id("p")

        # Parse name into first/last
        parts = name.strip().split(None, 1)
        first_name = parts[0] if parts else name.strip()
        last_name = parts[1] if len(parts) > 1 else None

        canonical = normalize_name(name)

        self.conn.execute(
            """
            INSERT INTO people (id, canonical_name, display_name, first_name, last_name,
                                importance, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 3, 0, ?, ?)
            """,
            (person_id, canonical, name.strip(), first_name, last_name, now, now),
        )

        # Add WhatsApp JID as identifier
        if wa_jid:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO person_identifiers (person_id, type, value, normalized, source, added_at)
                VALUES (?, 'wa_jid', ?, ?, 'whatsapp_group', ?)
                """,
                (person_id, wa_jid, wa_jid, now),
            )

            # Also add phone extracted from JID
            phone = _jid_to_phone(wa_jid)
            if phone:
                normalized_phone = normalize_phone(phone)
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO person_identifiers (person_id, type, value, normalized, source, added_at)
                    VALUES (?, 'phone', ?, ?, 'whatsapp_group', ?)
                    """,
                    (person_id, phone, normalized_phone, now),
                )

        return person_id

    def resolve_all(self) -> dict[str, int]:
        """Resolve unresolved group_members to existing or new person records.

        Resolution order:
        1. Match wa_jid to person_identifiers (wa_jid type)
        2. Extract phone from wa_jid, match to contact_point/person_identifiers
        3. Fuzzy match display name to people.canonical_name (>= 88)
        4. For unmatched members with good pushnames: create new person

        Returns: {resolved: N, created: N, unresolved: N}
        """
        unresolved = self.conn.execute(
            """
            SELECT gm.rowid, gm.group_id, gm.wa_jid, gm.name
            FROM group_members gm
            WHERE gm.person_id IS NULL
            """
        ).fetchall()

        if not unresolved:
            return {"resolved": 0, "created": 0, "unresolved": 0}

        resolved = 0
        created = 0
        still_unresolved = 0

        for member in unresolved:
            wa_jid = member["wa_jid"]
            name = member["name"]
            person_id = None

            # Strategy 1: Match by wa_jid directly
            if wa_jid:
                person_id = self._find_by_wa_jid(wa_jid)

            # Strategy 2: Extract phone from wa_jid, search by phone
            if not person_id and wa_jid:
                phone = _jid_to_phone(wa_jid)
                if phone:
                    person_id = self._find_by_phone(phone)

            # Strategy 3: Fuzzy name match
            if not person_id and name:
                person_id = self._find_by_name(name)

            # Strategy 4: Create new person if good pushname
            if not person_id and _is_good_pushname(name):
                person_id = self._create_person(name, wa_jid)
                created += 1

            if person_id:
                self.conn.execute(
                    "UPDATE group_members SET person_id = ? WHERE rowid = ?",
                    (person_id, member["rowid"]),
                )
                if person_id and name and not _is_good_pushname(name):
                    # Only count as resolved (not created) for existing matches
                    pass
                resolved += 1
            else:
                still_unresolved += 1

        self.conn.commit()
        return {
            "resolved": resolved,
            "created": created,
            "unresolved": still_unresolved,
        }

    def resolve_from_bridge(self, bridge_url: str = "http://localhost:7601") -> dict[str, int]:
        """Resolve LID group members using the WhatsApp bridge.

        Queries the bridge for group member details (which include real JIDs),
        caches the LID→JID mapping, then runs standard resolution on the
        newly-discovered JIDs.

        Requires the whatsmeow bridge to be running. Fails gracefully if not.
        """
        import json
        import urllib.request

        stats = {"groups_queried": 0, "lid_mappings_cached": 0, "resolved": 0}

        # Ensure lid_cache table exists
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wa_lid_cache (
                lid TEXT PRIMARY KEY,
                jid TEXT NOT NULL,
                cached_at INTEGER NOT NULL
            )
        """)

        try:
            resp = urllib.request.urlopen(f"{bridge_url}/groups", timeout=5)
            groups = json.loads(resp.read())
        except Exception:
            return {"error": "bridge_unavailable", **stats}

        now = _now()
        for group in groups:
            group_jid = group.get("jid", "")
            participants = group.get("participants", [])
            stats["groups_queried"] += 1

            for p in participants:
                lid = p.get("lid")
                jid = p.get("jid")
                if lid and jid and jid.endswith("@s.whatsapp.net"):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO wa_lid_cache (lid, jid, cached_at) VALUES (?, ?, ?)",
                        (lid, jid, now),
                    )
                    stats["lid_mappings_cached"] += 1

        self.conn.commit()

        # Now resolve LID group members using the cache
        unresolved_lids = self.conn.execute(
            "SELECT gm.rowid, gm.wa_jid FROM group_members gm "
            "WHERE gm.person_id IS NULL AND gm.wa_jid LIKE '%@lid'"
        ).fetchall()

        for member in unresolved_lids:
            lid = member["wa_jid"]
            # Look up in cache
            cached = self.conn.execute(
                "SELECT jid FROM wa_lid_cache WHERE lid = ?", (lid,)
            ).fetchone()
            if not cached:
                continue

            real_jid = cached["jid"]
            # Try to find person by this JID
            person_id = self._find_by_wa_jid(real_jid)
            if not person_id:
                phone = _jid_to_phone(real_jid)
                if phone:
                    person_id = self._find_by_phone(phone)

            if person_id:
                self.conn.execute(
                    "UPDATE group_members SET person_id = ? WHERE rowid = ?",
                    (person_id, member["rowid"]),
                )
                stats["resolved"] += 1

        self.conn.commit()
        return stats
