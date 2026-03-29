"""People Intelligence — Contact Resolver.

Resolves natural language references ("Faisal", "my mom", "Ballan") to a
person_id + contact record by querying the people DB directly.  No JSON
cache, no YAML alias file — everything comes from SQLite.

Tiers:
  0 — Alias lookup   (aliases table: relationships, nicknames, groups)
  1 — Exact name      (first_name, last_name, canonical_name)
  2 — Frequency rank  (relationship_state.msg_count_30d + last_interaction_at)
  3 — Phonetic match  (Arabic transliteration variants)
  4 — Fuzzy/substring (LIKE on canonical_name, organization)

Usage:
  python3 resolver.py "faisal"
  python3 resolver.py "my mom"
  python3 resolver.py --batch "mom" "ballan" "faisal"
  python3 resolver.py --json "faisal"

From Python:
  from resolver import resolve_contact
  result = resolve_contact("Faisal")
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
if str(_PEOPLE_SERVICE) not in sys.path:
    sys.path.insert(0, str(_PEOPLE_SERVICE))

from db import connect, now_ts

# ── Phonetic matching for Arabic transliterations ─────────────────────

PHONETIC_GROUPS: dict[str, list[str]] = {
    "muhammad": ["muhammad", "mohammed", "mohammad", "muhammed", "mohamed", "mohamad"],
    "hamza": ["hamza", "hamzah", "hamzeh", "humza"],
    "ahmad": ["ahmad", "ahmed", "ahmet"],
    "omar": ["omar", "omer", "umar", "umair"],
    "ayesha": ["ayesha", "aisha", "aysha", "aaisha", "aishah"],
    "fatima": ["fatima", "fatimah", "faatima"],
    "yusuf": ["yusuf", "yousuf", "yousef", "yosef", "youssef", "joseph"],
    "ibrahim": ["ibrahim", "ebrahim", "ibraheem"],
    "zain": ["zain", "zayn", "zein"],
    "hassan": ["hassan", "hasan", "hasen"],
    "hussain": ["hussain", "husain", "husein", "hussein", "hossein"],
    "tariq": ["tariq", "tarek", "tareq", "tarik"],
    "bilal": ["bilal", "bilaal", "belal"],
    "usama": ["usama", "osama", "usamah"],
    "imran": ["imran", "emran", "imraan"],
    "asma": ["asma", "asmaa", "asma'"],
    "maryam": ["maryam", "mariam", "maryum", "miriam"],
    "sana": ["sana", "sanaa", "thana"],
    "talha": ["talha", "talhah", "talhat"],
    "adnan": ["adnan", "adnaan"],
    "qasim": ["qasim", "kasim", "qassim", "kasem"],
    "sohail": ["sohail", "suhail", "soheil", "suhayl"],
    "abdullah": ["abdullah", "abdallah", "abdulla"],
    "ali": ["ali", "aly"],
    "faisal": ["faisal", "faysal", "feisal"],
    "idris": ["idris", "idrees", "idriss", "edris"],
    "shareef": ["shareef", "sharif", "sherif", "shreef"],
    "zeeshan": ["zeeshan", "zishan", "zeshan"],
    "hisham": ["hisham", "hesham", "hicham"],
    "khalid": ["khalid", "khaled"],
    "rashid": ["rashid", "rasheed", "rashed"],
    "nasir": ["nasir", "nasser", "naseer", "nasr"],
    "samir": ["samir", "sameer"],
    "nadia": ["nadia", "nadya", "naadya"],
}

# Reverse lookup: variant spelling -> canonical form
_PHONETIC_REVERSE: dict[str, str] = {}
for _canonical, _variants in PHONETIC_GROUPS.items():
    for _v in _variants:
        _PHONETIC_REVERSE[_v] = _canonical


def phonetic_key(name: str) -> str:
    """Return the canonical phonetic key for a name, or the lowered name itself."""
    return _PHONETIC_REVERSE.get(name.lower(), name.lower())


# ── Prefixes to strip from references ────────────────────────────────

PREFIXES_TO_STRIP = [
    "my sister ", "my brother ", "my uncle ", "my aunt ",
    "my cousin ", "my wife ", "my husband ", "my ",
]

# ── Multi-person role phrases ────────────────────────────────────────

MULTI_PERSON_PHRASES = [
    "brother in law", "brother-in-law", "bil",
    "sister in law", "sister-in-law", "sil",
]


# ── Internal helpers ─────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or None."""
    return dict(row) if row else None


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def _build_contact(conn: sqlite3.Connection, person: dict) -> dict:
    """Build a rich contact dict from a people row."""
    pid = person["id"]

    # Identifiers
    idents = conn.execute(
        "SELECT type, value, normalized, is_primary, label "
        "FROM person_identifiers WHERE person_id = ?", (pid,)
    ).fetchall()

    phones = []
    emails = []
    wa_jid = None
    for ident in idents:
        t = ident["type"]
        v = ident["value"]
        if t == "phone":
            phones.append(v)
        elif t == "email":
            emails.append(v)
        elif t == "wa_jid" and v.endswith("@s.whatsapp.net"):
            wa_jid = v

    # Metadata
    meta = _row_to_dict(conn.execute(
        "SELECT organization, preferred_channel, city, country, "
        "birthday, job_title, how_met "
        "FROM contact_metadata WHERE person_id = ?", (pid,)
    ).fetchone())

    return {
        "person_id": pid,
        "name": person["canonical_name"],
        "display_name": person.get("display_name") or person["canonical_name"],
        "first_name": person.get("first_name", ""),
        "last_name": person.get("last_name", ""),
        "nickname": person.get("nickname", ""),
        "importance": person.get("importance", 3),
        "phones": phones,
        "emails": emails,
        "wa_jid": wa_jid,
        "organization": (meta or {}).get("organization", ""),
        "preferred_channel": (meta or {}).get("preferred_channel", ""),
        "city": (meta or {}).get("city", ""),
        "country": (meta or {}).get("country", ""),
    }


def _build_group_contact(conn: sqlite3.Connection, group: dict) -> dict:
    """Build a contact dict from a group row."""
    return {
        "person_id": None,
        "group_id": group["id"],
        "name": group["name"],
        "display_name": group["name"],
        "type": group.get("type", ""),
        "wa_jid": group.get("wa_jid", ""),
        "member_count": group.get("member_count", 0),
        "is_group": True,
    }


def _resolve_channel(contact: dict) -> str:
    """Determine the best channel to reach a contact."""
    # Check explicit override from metadata
    preferred = contact.get("preferred_channel", "")
    if preferred:
        return preferred

    # Default priority: WhatsApp > iMessage > email > SMS
    if contact.get("wa_jid"):
        return "whatsapp"
    if contact.get("emails"):
        return "email"
    if contact.get("phones"):
        return "sms"
    return "unknown"


def _make_result(
    *,
    person_id: str | None = None,
    contact: dict | None = None,
    candidates: list[dict] | None = None,
    confidence: float = 0.0,
    tier: int = -1,
    tier_name: str = "no_match",
    channel: str = "unknown",
    resolved: bool = False,
    is_group: bool = False,
) -> dict:
    """Build a standardised result dict."""
    return {
        "person_id": person_id,
        "contact": contact,
        "candidates": candidates or [],
        "confidence": confidence,
        "tier": tier,
        "tier_name": tier_name,
        "channel": channel,
        "resolved": resolved,
        "is_group": is_group,
    }


def _rank_by_frequency(
    conn: sqlite3.Connection,
    candidates: list[dict],
) -> list[tuple[dict, int]]:
    """Rank candidate contacts by msg_count_30d + last_interaction_at.

    Returns list of (contact, score) tuples sorted descending.
    """
    if not candidates:
        return []

    pids = [c["person_id"] for c in candidates if c.get("person_id")]
    if not pids:
        return [(c, 0) for c in candidates]

    placeholders = ",".join("?" * len(pids))
    rows = conn.execute(
        f"SELECT person_id, msg_count_30d, last_interaction_at "
        f"FROM relationship_state WHERE person_id IN ({placeholders})",
        pids,
    ).fetchall()

    state_by_pid: dict[str, dict] = {}
    for r in rows:
        state_by_pid[r["person_id"]] = dict(r)

    scored = []
    for c in candidates:
        pid = c.get("person_id", "")
        st = state_by_pid.get(pid)
        if st:
            # Composite score: message count (primary) + recency bonus
            msg_score = st.get("msg_count_30d") or 0
            last_ts = st.get("last_interaction_at") or 0
            # Recency: seconds within last 30 days mapped to 0-10 bonus
            if last_ts > 0:
                age_days = max(0, (time.time() - last_ts) / 86400)
                recency_bonus = max(0, 10 - age_days)
            else:
                recency_bonus = 0
            score = msg_score + recency_bonus
        else:
            score = 0
        scored.append((c, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _strip_reference(ref_lower: str) -> list[str]:
    """Generate variant forms of a reference by stripping common prefixes.

    "my mom" -> ["my mom", "mom"]
    "my brother in law imran" -> ["my brother in law imran", "brother in law imran"]
    """
    variants = [ref_lower]
    for prefix in PREFIXES_TO_STRIP:
        if ref_lower.startswith(prefix):
            stripped = ref_lower[len(prefix):]
            if stripped:
                variants.append(stripped)
    # Also try individual words for single-word aliases
    if " " in ref_lower:
        words = ref_lower.split()
        variants.extend(words)
    return variants


# ── Tier 0: Alias lookup ─────────────────────────────────────────────

def _tier0_alias(
    conn: sqlite3.Connection,
    ref_lower: str,
) -> dict | None:
    """Tier 0 — look up the reference in the aliases table.

    Handles:
    - Direct alias match ("mom", "ballan", "wife")
    - Prefix stripping ("my mom" -> "mom")
    - Group aliases ("family group" -> group info)
    - Multi-person roles with qualification ("brother in law imran")
    """
    variants = _strip_reference(ref_lower)

    # Check multi-person role phrases first
    for phrase in MULTI_PERSON_PHRASES:
        if phrase not in ref_lower:
            continue

        # Find all aliases whose type is 'relationship' that match the phrase
        # First check if there's a qualifying name after the phrase
        # e.g., "brother in law imran" -> qualifier = "imran"
        qualifier = None
        for p in [phrase, phrase.replace("-", " ")]:
            idx = ref_lower.find(p)
            if idx >= 0:
                after = ref_lower[idx + len(p):].strip()
                if after:
                    qualifier = after
                    break
            # Also check "my brother in law imran"
            for prefix in PREFIXES_TO_STRIP:
                if ref_lower.startswith(prefix):
                    inner = ref_lower[len(prefix):]
                    idx2 = inner.find(p)
                    if idx2 >= 0:
                        after2 = inner[idx2 + len(p):].strip()
                        if after2:
                            qualifier = after2
                            break

        # Get all aliases matching this phrase pattern
        # The aliases table stores specific names, not the phrase itself
        # So "brother in law" isn't an alias — "imran" is an alias that
        # happens to be a brother in law. We check if the qualifier
        # matches an alias.
        if qualifier:
            row = conn.execute(
                "SELECT alias, person_id, group_id, type FROM aliases "
                "WHERE alias = ? COLLATE NOCASE",
                (qualifier,),
            ).fetchone()
            if row and row["person_id"]:
                person = _row_to_dict(conn.execute(
                    "SELECT * FROM people WHERE id = ?", (row["person_id"],)
                ).fetchone())
                if person:
                    contact = _build_contact(conn, person)
                    return _make_result(
                        person_id=person["id"],
                        contact=contact,
                        candidates=[contact],
                        confidence=1.0,
                        tier=0,
                        tier_name="alias_qualified",
                        channel=_resolve_channel(contact),
                        resolved=True,
                    )

    # Standard alias lookup — try each variant
    for variant in variants:
        row = conn.execute(
            "SELECT alias, person_id, group_id, type FROM aliases "
            "WHERE alias = ? COLLATE NOCASE",
            (variant,),
        ).fetchone()

        if not row:
            continue

        # Group alias
        if row["group_id"]:
            group = _row_to_dict(conn.execute(
                "SELECT * FROM groups WHERE id = ?", (row["group_id"],)
            ).fetchone())
            if group:
                contact = _build_group_contact(conn, group)
                return _make_result(
                    contact=contact,
                    candidates=[contact],
                    confidence=1.0,
                    tier=0,
                    tier_name="group_alias",
                    channel="whatsapp_group" if group.get("wa_jid") else "group",
                    resolved=True,
                    is_group=True,
                )

        # Person alias
        if row["person_id"]:
            person = _row_to_dict(conn.execute(
                "SELECT * FROM people WHERE id = ?", (row["person_id"],)
            ).fetchone())
            if person:
                contact = _build_contact(conn, person)
                return _make_result(
                    person_id=person["id"],
                    contact=contact,
                    candidates=[contact],
                    confidence=1.0,
                    tier=0,
                    tier_name="alias_map",
                    channel=_resolve_channel(contact),
                    resolved=True,
                )

    return None


# ── Tier 1: Exact name lookup ────────────────────────────────────────

def _tier1_exact_name(
    conn: sqlite3.Connection,
    ref_lower: str,
) -> dict | None:
    """Tier 1 — exact match on first_name, last_name, or canonical_name."""
    ref_parts = ref_lower.split()

    # Full name match (highest confidence)
    rows = conn.execute(
        "SELECT * FROM people WHERE canonical_name = ? COLLATE NOCASE",
        (ref_lower,),
    ).fetchall()
    if not rows and len(ref_parts) >= 2:
        # Try "First Last" with partial last name
        first_part = ref_parts[0]
        last_part = " ".join(ref_parts[1:])
        rows = conn.execute(
            "SELECT * FROM people "
            "WHERE first_name = ? COLLATE NOCASE AND last_name LIKE ? COLLATE NOCASE",
            (first_part, f"%{last_part}%"),
        ).fetchall()

    if len(rows) == 1:
        person = dict(rows[0])
        contact = _build_contact(conn, person)
        return _make_result(
            person_id=person["id"],
            contact=contact,
            candidates=[contact],
            confidence=0.98,
            tier=1,
            tier_name="exact_full_name",
            channel=_resolve_channel(contact),
            resolved=True,
        )
    if len(rows) > 1:
        contacts = [_build_contact(conn, dict(r)) for r in rows]
        return _make_result(
            person_id=contacts[0]["person_id"],
            contact=contacts[0],
            candidates=contacts,
            confidence=0.50,
            tier=1,
            tier_name="exact_full_name_ambiguous",
            channel=_resolve_channel(contacts[0]),
            resolved=False,
        )

    # Last name match (unique last names are strong signals)
    last_rows = conn.execute(
        "SELECT * FROM people WHERE last_name = ? COLLATE NOCASE",
        (ref_lower,),
    ).fetchall()
    if len(last_rows) == 1:
        person = dict(last_rows[0])
        contact = _build_contact(conn, person)
        return _make_result(
            person_id=person["id"],
            contact=contact,
            candidates=[contact],
            confidence=0.95,
            tier=1,
            tier_name="exact_last_name",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # First name match
    first_rows = conn.execute(
        "SELECT * FROM people WHERE first_name = ? COLLATE NOCASE",
        (ref_lower,),
    ).fetchall()
    if len(first_rows) == 1:
        person = dict(first_rows[0])
        contact = _build_contact(conn, person)
        return _make_result(
            person_id=person["id"],
            contact=contact,
            candidates=[contact],
            confidence=0.95,
            tier=1,
            tier_name="exact_first_name",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Nickname match
    nick_rows = conn.execute(
        "SELECT * FROM people WHERE nickname = ? COLLATE NOCASE",
        (ref_lower,),
    ).fetchall()
    if len(nick_rows) == 1:
        person = dict(nick_rows[0])
        contact = _build_contact(conn, person)
        return _make_result(
            person_id=person["id"],
            contact=contact,
            candidates=[contact],
            confidence=0.93,
            tier=1,
            tier_name="exact_nickname",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Collect all exact-tier matches for disambiguation in tier 2
    all_exact = []
    seen_ids = set()
    for row_set in [last_rows, first_rows, nick_rows]:
        for r in row_set:
            pid = r["id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_exact.append(dict(r))

    if all_exact:
        contacts = [_build_contact(conn, p) for p in all_exact]
        # Return these as candidates for tier 2 to rank
        return _make_result(
            person_id=contacts[0]["person_id"],
            contact=contacts[0],
            candidates=contacts,
            confidence=0.40,
            tier=1,
            tier_name="exact_name_ambiguous",
            channel=_resolve_channel(contacts[0]),
            resolved=False,
        )

    return None


# ── Tier 2: Frequency ranking ────────────────────────────────────────

def _tier2_frequency(
    conn: sqlite3.Connection,
    candidates: list[dict],
    context: str | None = None,
) -> dict | None:
    """Tier 2 — rank ambiguous candidates by message frequency and recency.

    Also attempts context-based disambiguation using organization field.
    """
    if len(candidates) < 2:
        return None

    scored = _rank_by_frequency(conn, candidates)

    # If top candidate dominates (2x the second)
    if (scored[0][1] > 0
            and (len(scored) < 2 or scored[0][1] > scored[1][1] * 2)):
        contact = scored[0][0]
        return _make_result(
            person_id=contact["person_id"],
            contact=contact,
            candidates=[s[0] for s in scored],
            confidence=min(0.90, 0.70 + (scored[0][1] / 50)),
            tier=2,
            tier_name="frequency_ranking",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Context-based disambiguation
    if context:
        context_lower = context.lower()
        for contact, _score in scored:
            org = (contact.get("organization") or "").lower()
            if org and any(word in context_lower for word in org.split() if len(word) > 2):
                return _make_result(
                    person_id=contact["person_id"],
                    contact=contact,
                    candidates=[s[0] for s in scored],
                    confidence=0.85,
                    tier=2,
                    tier_name="context_disambiguation",
                    channel=_resolve_channel(contact),
                    resolved=True,
                )

    # Importance-based tiebreak: prefer importance=1 (inner circle)
    by_importance = sorted(scored, key=lambda x: (x[0].get("importance", 3), -x[1]))
    if by_importance[0][0].get("importance", 3) < by_importance[1][0].get("importance", 3):
        contact = by_importance[0][0]
        return _make_result(
            person_id=contact["person_id"],
            contact=contact,
            candidates=[s[0] for s in scored],
            confidence=0.75,
            tier=2,
            tier_name="importance_ranking",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Cannot disambiguate
    top = scored[0][0]
    return _make_result(
        person_id=top["person_id"],
        contact=top,
        candidates=[s[0] for s in scored],
        confidence=0.40,
        tier=2,
        tier_name="ambiguous_frequency",
        channel=_resolve_channel(top),
        resolved=False,
    )


# ── Tier 3: Phonetic matching ────────────────────────────────────────

def _tier3_phonetic(
    conn: sqlite3.Connection,
    ref_lower: str,
    context: str | None = None,
) -> dict | None:
    """Tier 3 — match using phonetic variants of Arabic names."""
    ref_ph = phonetic_key(ref_lower)
    ref_parts = ref_lower.split()
    ref_parts_ph = [phonetic_key(p) for p in ref_parts]

    # Get all canonical forms from the phonetic group
    variants = PHONETIC_GROUPS.get(ref_ph)
    if not variants and len(ref_parts) >= 2:
        # Multi-word: check each part
        pass
    elif not variants:
        # Not in phonetic groups — skip tier
        return None

    # Single-word reference: search first_name and last_name
    matches = []
    if variants:
        placeholders = ",".join("?" * len(variants))
        rows = conn.execute(
            f"SELECT * FROM people "
            f"WHERE LOWER(first_name) IN ({placeholders}) "
            f"OR LOWER(last_name) IN ({placeholders})",
            variants + variants,
        ).fetchall()
        matches = _rows_to_dicts(rows)

    # Multi-word reference: match first phonetic + last phonetic
    if not matches and len(ref_parts_ph) >= 2:
        first_variants = PHONETIC_GROUPS.get(ref_parts_ph[0], [ref_parts[0]])
        last_variants = PHONETIC_GROUPS.get(ref_parts_ph[-1], [ref_parts[-1]])
        fp = ",".join("?" * len(first_variants))
        lp = ",".join("?" * len(last_variants))
        rows = conn.execute(
            f"SELECT * FROM people "
            f"WHERE LOWER(first_name) IN ({fp}) AND LOWER(last_name) IN ({lp})",
            first_variants + last_variants,
        ).fetchall()
        matches = _rows_to_dicts(rows)

    if not matches:
        return None

    contacts = [_build_contact(conn, m) for m in matches]

    if len(contacts) == 1:
        contact = contacts[0]
        return _make_result(
            person_id=contact["person_id"],
            contact=contact,
            candidates=contacts,
            confidence=0.85,
            tier=3,
            tier_name="phonetic_match",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Multiple phonetic matches — try frequency ranking
    result = _tier2_frequency(conn, contacts, context)
    if result and result["resolved"]:
        result["tier"] = 3
        result["tier_name"] = "phonetic_with_frequency"
        result["confidence"] = min(result["confidence"], 0.80)
        return result

    # Return ambiguous
    scored = _rank_by_frequency(conn, contacts)
    top = scored[0][0] if scored else contacts[0]
    return _make_result(
        person_id=top["person_id"],
        contact=top,
        candidates=[s[0] for s in scored] if scored else contacts,
        confidence=0.35,
        tier=3,
        tier_name="phonetic_ambiguous",
        channel=_resolve_channel(top),
        resolved=False,
    )


# ── Tier 4: Fuzzy / substring ────────────────────────────────────────

def _tier4_fuzzy(
    conn: sqlite3.Connection,
    ref_lower: str,
    context: str | None = None,
) -> dict | None:
    """Tier 4 — LIKE queries on canonical_name and organization."""
    # Substring in canonical_name
    rows = conn.execute(
        "SELECT * FROM people WHERE canonical_name LIKE ? COLLATE NOCASE",
        (f"%{ref_lower}%",),
    ).fetchall()

    # Also search organization
    if not rows:
        rows = conn.execute(
            "SELECT p.* FROM people p "
            "JOIN contact_metadata cm ON cm.person_id = p.id "
            "WHERE cm.organization LIKE ? COLLATE NOCASE",
            (f"%{ref_lower}%",),
        ).fetchall()

    # Multi-word: check if all parts match somewhere
    if not rows:
        ref_parts = ref_lower.split()
        if len(ref_parts) >= 2:
            # Build AND condition for each part
            conditions = " AND ".join(
                "(canonical_name LIKE ? COLLATE NOCASE)" for _ in ref_parts
            )
            params = [f"%{p}%" for p in ref_parts]
            rows = conn.execute(
                f"SELECT * FROM people WHERE {conditions}",
                params,
            ).fetchall()

    if not rows:
        return None

    matches = _rows_to_dicts(rows)
    contacts = [_build_contact(conn, m) for m in matches]

    if len(contacts) == 1:
        contact = contacts[0]
        return _make_result(
            person_id=contact["person_id"],
            contact=contact,
            candidates=contacts,
            confidence=0.80,
            tier=4,
            tier_name="fuzzy_match",
            channel=_resolve_channel(contact),
            resolved=True,
        )

    # Multiple fuzzy matches — rank
    scored = _rank_by_frequency(conn, contacts)
    top = scored[0][0] if scored else contacts[0]
    return _make_result(
        person_id=top["person_id"],
        contact=top,
        candidates=[s[0] for s in scored[:5]] if scored else contacts[:5],
        confidence=0.30,
        tier=4,
        tier_name="fuzzy_ambiguous",
        channel=_resolve_channel(top),
        resolved=False,
    )


# ── Public API ───────────────────────────────────────────────────────

def resolve_contact(
    reference: str,
    context: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Resolve a natural language contact reference to a person_id + contact.

    Args:
        reference: Natural language reference ("Faisal", "my mom", "Ballan")
        context: Optional conversation context for disambiguation
        conn: Optional pre-existing DB connection (avoids reconnect)

    Returns:
        {
            "person_id": str or None,
            "contact": {...},
            "candidates": [...],
            "confidence": float,
            "tier": int,
            "tier_name": str,
            "channel": str,
            "resolved": bool,
            "is_group": bool,
        }
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()

    try:
        ref_lower = reference.strip().lower()
        if not ref_lower:
            return _make_result()

        # Tier 0: Alias lookup
        result = _tier0_alias(conn, ref_lower)
        if result:
            return result

        # Tier 1: Exact name lookup
        result = _tier1_exact_name(conn, ref_lower)
        if result:
            # If unambiguous, return directly
            if result["resolved"]:
                return result
            # If ambiguous, fall through to tier 2 with candidates
            candidates = result["candidates"]
            freq_result = _tier2_frequency(conn, candidates, context)
            if freq_result:
                return freq_result
            return result

        # Tier 3: Phonetic matching
        result = _tier3_phonetic(conn, ref_lower, context)
        if result:
            return result

        # Tier 4: Fuzzy / substring
        result = _tier4_fuzzy(conn, ref_lower, context)
        if result:
            return result

        # No match
        return _make_result()

    finally:
        if own_conn:
            conn.close()


def resolve_contacts(
    references: list[str],
    context: str | None = None,
) -> list[dict]:
    """Resolve multiple contact references in a single call.

    Shares one DB connection across all lookups for efficiency.
    """
    conn = connect()
    try:
        return [
            resolve_contact(ref, context=context, conn=conn)
            for ref in references
        ]
    finally:
        conn.close()


def log_interaction(
    person_id: str,
    channel: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Update relationship_state after an interaction.

    Increments msg_count_30d and updates last_interaction_at/channel.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()

    try:
        ts = now_ts()
        existing = conn.execute(
            "SELECT person_id FROM relationship_state WHERE person_id = ?",
            (person_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE relationship_state SET "
                "last_interaction_at = ?, "
                "last_interaction_channel = ?, "
                "msg_count_30d = msg_count_30d + 1, "
                "interaction_count_30d = interaction_count_30d + 1, "
                "computed_at = ? "
                "WHERE person_id = ?",
                (ts, channel, ts, person_id),
            )
        else:
            conn.execute(
                "INSERT INTO relationship_state "
                "(person_id, last_interaction_at, last_interaction_channel, "
                "msg_count_30d, interaction_count_30d, computed_at) "
                "VALUES (?, ?, ?, 1, 1, ?)",
                (person_id, ts, channel, ts),
            )

        conn.commit()
    finally:
        if own_conn:
            conn.close()


# ── CLI ──────────────────────────────────────────────────────────────

def _format_result(ref: str, result: dict) -> str:
    """Format a resolution result for terminal display."""
    lines = [f"  Resolving: \"{ref}\""]

    if result["is_group"]:
        c = result["contact"]
        lines.append(f"  -> {c['name']}  [group]")
        lines.append(f"     channel: {result['channel']}")
        if c.get("wa_jid"):
            lines.append(f"     wa_jid: {c['wa_jid']}")
        lines.append(f"     confidence: {result['confidence']:.0%} (tier {result['tier']}: {result['tier_name']})")
        return "\n".join(lines)

    if result["resolved"]:
        c = result["contact"]
        lines.append(f"  -> {c['name']}  [{c.get('person_id', '?')}]")
        lines.append(f"     confidence: {result['confidence']:.0%} (tier {result['tier']}: {result['tier_name']})")
        lines.append(f"     channel: {result['channel']}")
        if c.get("phones"):
            lines.append(f"     phone: {c['phones'][0]}")
        if c.get("wa_jid"):
            lines.append(f"     wa_jid: {c['wa_jid']}")
        if c.get("organization"):
            lines.append(f"     org: {c['organization']}")
        if len(result["candidates"]) > 1:
            lines.append(f"     ({len(result['candidates'])} candidates — top pick shown)")

    elif result["candidates"]:
        lines.append(f"  ? Ambiguous — {len(result['candidates'])} candidates (tier {result['tier']}: {result['tier_name']})")
        for i, c in enumerate(result["candidates"][:5]):
            org = f" ({c['organization']})" if c.get("organization") else ""
            pid = c.get("person_id", "?")
            lines.append(f"     {i+1}. {c['name']}{org}  [{pid}]")

    else:
        lines.append("  x No match found")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="People Intelligence — Contact Resolver",
    )
    parser.add_argument(
        "reference", nargs="?",
        help="Contact reference to resolve",
    )
    parser.add_argument(
        "--context", type=str,
        help="Conversation context for disambiguation",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON output",
    )
    parser.add_argument(
        "--batch", nargs="+",
        help="Resolve multiple references",
    )
    args = parser.parse_args()

    references = args.batch if args.batch else ([args.reference] if args.reference else [])

    if not references:
        parser.print_help()
        return

    results = resolve_contacts(references, context=args.context)

    for ref, result in zip(references, results):
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print()
            print(_format_result(ref, result))
            print()


if __name__ == "__main__":
    main()
