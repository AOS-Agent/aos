"""Identity Resolution Engine.

Takes RawClaim objects (dicts with contact data from source connectors) and
resolves them against people.db, performing:

  1. **Deterministic matching** — exact phone / email / JID lookup.
  2. **Fuzzy matching** — phonetic blocking + token-sort name similarity.
  3. **Golden record building** — picks the best value per field from all
     source records, weighted by source priority.

The engine writes to the ontology tables introduced by migration 027
(``contact_point``, ``source_record``, ``hygiene_queue``) when they exist,
and falls back to ``person_identifiers`` for reads when the ontology tables
are empty or absent.

Claim dict shape (all fields optional except at least one identifier)::

    {
        "source_type": "apple_contacts",   # required
        "source_id": "AB:12345",           # external ID in the source system
        "name": "Mohammed Tarek",
        "first_name": "Mohammed",
        "last_name": "Tarek",
        "phones": ["+971501234567"],
        "emails": ["m@example.com"],
        "wa_jids": ["971501234567@s.whatsapp.net"],
        "organization": "Acme Corp",
        "job_title": "CTO",
        "city": "Dubai",
        "country": "AE",
        "birthday": "1990-05-15",
        "raw_data": { ... },               # preserved verbatim in source_record
    }
"""

from __future__ import annotations

import json
import logging
import sqlite3
import string
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .normalize import (
    normalize_email,
    normalize_name,
    normalize_phone,
    phonetic_key,
)

log = logging.getLogger(__name__)

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

# ── Source priority for golden record field resolution ───────────────────
# Higher number = more trusted.  Manual edits always win.

SOURCE_PRIORITY: dict[str, int] = {
    "manual": 100,
    "apple_contacts": 80,
    "whatsapp": 60,
    "imessage": 50,
    "telegram": 40,
    "extraction": 30,
    "pushname": 20,
}

# ── Thresholds ──────────────────────────────────────────────────────────

_FUZZY_AUTO_MATCH = 92     # >= this score: auto-link (confidence 0.88)
_FUZZY_REVIEW_MIN = 80     # 80..91: queue for human review
_GROUP_BOOST = 0.04        # confidence bonus per shared WhatsApp group (max 3)


# ── ID generation ───────────────────────────────────────────────────────

_ID_ALPHABET = string.ascii_lowercase + string.digits


def _nanoid(prefix: str, length: int = 8) -> str:
    """Generate a short random ID with the given prefix."""
    return prefix + "".join(random.choices(_ID_ALPHABET, k=length))


def _now_epoch() -> int:
    return int(time.time())


# ── Result dataclasses ──────────────────────────────────────────────────


@dataclass
class MatchResult:
    """Outcome of resolving a single claim."""

    person_id: str | None = None
    confidence: float = 0.0
    match_type: str = "none"  # exact_phone, exact_email, exact_jid, fuzzy_name, new
    evidence: list[str] = field(default_factory=list)


@dataclass
class ResolveResult:
    """Aggregate outcome of resolving a batch of claims."""

    matched: list[MatchResult] = field(default_factory=list)
    new_persons: int = 0
    merged: int = 0
    queued_for_review: int = 0


# ── Schema detection ────────────────────────────────────────────────────


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


# ── IdentityResolver ───────────────────────────────────────────────────


class IdentityResolver:
    """Resolve raw claims into unique person records.

    Parameters
    ----------
    conn : sqlite3.Connection or None
        An existing connection to ``people.db``.  If *None*, a new connection
        is opened to :data:`DB_PATH`.
    """

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(str(DB_PATH))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._owns_conn = True

        # Detect whether the ontology tables from migration 027 are present.
        self._has_ontology = _table_exists(self._conn, "contact_point")

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()

    # ── Public: single claim ─────────────────────────────────────────

    def resolve_claim(self, claim: dict[str, Any]) -> MatchResult:
        """Resolve a single claim dict against the people database.

        See module docstring for the expected claim shape.

        Returns a :class:`MatchResult` indicating whether the claim matched
        an existing person, created a new one, or was queued for review.
        """
        source_type = claim.get("source_type", "unknown")

        # ── Normalize identifiers from the claim ────────────────────
        norm_phones: list[str] = []
        for p in claim.get("phones", []):
            n = normalize_phone(p)
            if n:
                norm_phones.append(n)

        norm_emails: list[str] = []
        for e in claim.get("emails", []):
            n = normalize_email(e)
            if n:
                norm_emails.append(n)

        norm_jids: list[str] = []
        for j in claim.get("wa_jids", []):
            stripped = j.strip()
            if stripped:
                norm_jids.append(stripped)

        # ── Step 1: Deterministic matching ──────────────────────────
        matched_pids: set[str] = set()
        evidence: list[str] = []

        # Phone lookup
        for phone in norm_phones:
            pid = self._lookup_identifier("phone", phone)
            if pid:
                matched_pids.add(pid)
                evidence.append(f"phone={phone}")

        # Email lookup
        for email in norm_emails:
            pid = self._lookup_identifier("email", email)
            if pid:
                matched_pids.add(pid)
                evidence.append(f"email={email}")

        # WhatsApp JID lookup
        for jid in norm_jids:
            pid = self._lookup_identifier("wa_jid", jid)
            if pid:
                matched_pids.add(pid)
                evidence.append(f"wa_jid={jid}")

        if len(matched_pids) == 1:
            # Clean deterministic match
            person_id = matched_pids.pop()
            match_type = "exact_phone" if norm_phones else (
                "exact_email" if norm_emails else "exact_jid"
            )
            sr_id = self._insert_claim_data(person_id, claim)
            self.build_golden_record(person_id)
            return MatchResult(
                person_id=person_id,
                confidence=0.95,
                match_type=match_type,
                evidence=evidence,
            )

        if len(matched_pids) > 1:
            # Conflict: identifiers point to different people.
            # Queue for human review rather than auto-merging.
            pids = list(matched_pids)
            self._queue_hygiene(
                action_type="merge_conflict",
                person_a_id=pids[0],
                person_b_id=pids[1],
                confidence=0.90,
                reason=(
                    f"Claim from {source_type} matched multiple persons: "
                    f"{', '.join(pids)}. Evidence: {'; '.join(evidence)}"
                ),
            )
            # Attach data to the first match for now
            sr_id = self._insert_claim_data(pids[0], claim)
            return MatchResult(
                person_id=pids[0],
                confidence=0.70,
                match_type="conflict",
                evidence=evidence + [f"conflict_with={','.join(pids[1:])}"],
            )

        # ── Step 2: Fuzzy matching ──────────────────────────────────
        raw_name = claim.get("name", "")
        if not raw_name:
            fn = claim.get("first_name", "")
            ln = claim.get("last_name", "")
            raw_name = f"{fn} {ln}".strip()

        if raw_name:
            canonical, first, last = normalize_name(raw_name)
            if canonical:
                result = self._fuzzy_match(canonical, claim)
                if result is not None:
                    return result

        # ── Step 3: No match — create new person ────────────────────
        person_id = self._create_person(claim)
        sr_id = self._insert_claim_data(person_id, claim)
        self.build_golden_record(person_id)
        return MatchResult(
            person_id=person_id,
            confidence=1.0,
            match_type="new",
            evidence=[f"created from {source_type}"],
        )

    # ── Public: batch resolve ────────────────────────────────────────

    def resolve_claims(self, claims: list[dict[str, Any]]) -> ResolveResult:
        """Resolve a list of claims, tracking aggregate statistics.

        Returns a :class:`ResolveResult` with per-claim results and counters.
        """
        result = ResolveResult()
        for claim in claims:
            mr = self.resolve_claim(claim)
            result.matched.append(mr)
            if mr.match_type == "new":
                result.new_persons += 1
            elif mr.match_type == "conflict":
                result.queued_for_review += 1
            elif mr.match_type == "review":
                result.queued_for_review += 1
        return result

    # ── Public: golden record ────────────────────────────────────────

    def build_golden_record(self, person_id: str) -> None:
        """Rebuild the golden record for a person.

        For each field (name, phone, email, org, city, etc.) the engine
        examines all ``source_record`` rows for this person (or falls back
        to ``person_identifiers`` + ``contact_metadata``), picks the value
        from the highest-priority source, and updates the ``people`` and
        ``contact_metadata`` tables accordingly.

        Sets ``golden_record_at`` on the ``people`` row when the ontology
        column is available.
        """
        now = _now_epoch()

        if self._has_ontology:
            self._build_golden_from_source_records(person_id, now)
        else:
            # Pre-ontology: the existing data *is* the golden record.
            # Just ensure updated_at is current.
            self._conn.execute(
                "UPDATE people SET updated_at = ? WHERE id = ?",
                (now, person_id),
            )
            self._conn.commit()

    # ── Internal: identifier lookup ──────────────────────────────────

    def _lookup_identifier(self, id_type: str, normalized: str) -> str | None:
        """Look up a normalized identifier and return the person_id, or None.

        Checks ``contact_point`` first (ontology table), then falls back to
        ``person_identifiers`` (legacy table).
        """
        if self._has_ontology:
            row = self._conn.execute(
                "SELECT person_id FROM contact_point "
                "WHERE type = ? AND normalized = ? LIMIT 1",
                (id_type, normalized),
            ).fetchone()
            if row:
                return row[0] if isinstance(row, tuple) else row["person_id"]

        # Fall back to legacy table.
        # person_identifiers stores normalized phones as digits-only
        # (no leading +), so we need to strip for comparison.
        if id_type == "phone":
            # Try E.164 first, then digits-only
            for lookup_val in [normalized, normalized.lstrip("+")]:
                row = self._conn.execute(
                    "SELECT person_id FROM person_identifiers "
                    "WHERE type = ? AND (normalized = ? OR value = ?) LIMIT 1",
                    (id_type, lookup_val, normalized),
                ).fetchone()
                if row:
                    return row[0] if isinstance(row, tuple) else row["person_id"]
        else:
            row = self._conn.execute(
                "SELECT person_id FROM person_identifiers "
                "WHERE type = ? AND (normalized = ? OR value = ?) LIMIT 1",
                (id_type, normalized, normalized),
            ).fetchone()
            if row:
                return row[0] if isinstance(row, tuple) else row["person_id"]

        return None

    # ── Internal: fuzzy matching ─────────────────────────────────────

    def _fuzzy_match(
        self,
        canonical_name: str,
        claim: dict[str, Any],
    ) -> MatchResult | None:
        """Attempt fuzzy name matching using phonetic blocking.

        Returns a MatchResult if a match or review-queue decision was made,
        or None if no candidates were found in the phonetic block.
        """
        pkey = phonetic_key(canonical_name)

        # Find all people whose phonetic key matches (blocking step).
        # We compute phonetic keys on-the-fly from canonical_name since
        # there is no indexed phonetic_key column yet.
        candidates = self._find_phonetic_candidates(pkey)

        if not candidates:
            return None

        best_pid: str | None = None
        best_score: float = 0.0
        best_name: str = ""

        for cand_id, cand_name in candidates:
            raw_score = fuzz.token_sort_ratio(
                canonical_name.lower(), cand_name.lower()
            )
            # Phonetic bonus: if both names map to the same phonetic key,
            # the blocking step already proved these are known transliteration
            # variants.  Boost the effective score by up to 4 points so that
            # borderline cases (e.g. "Ahmed" vs "Ahmad") clear the auto-match
            # threshold instead of being queued for review.
            effective = min(100.0, raw_score + 4.0)

            if effective > best_score:
                best_score = effective
                best_pid = cand_id
                best_name = cand_name

        if best_score < _FUZZY_REVIEW_MIN:
            # No candidate is close enough — treat as distinct.
            return None

        # Confidence derived from the effective score (0..1 range).
        confidence = best_score / 100.0

        # Apply shared-group boost.
        source_type = claim.get("source_type", "")
        wa_jids = claim.get("wa_jids", [])
        if wa_jids and best_pid:
            shared = self._count_shared_groups(best_pid, wa_jids)
            boost = min(shared, 3) * _GROUP_BOOST
            confidence = min(1.0, confidence + boost)

        if best_score >= _FUZZY_AUTO_MATCH:
            # Auto-match
            sr_id = self._insert_claim_data(best_pid, claim)
            self.build_golden_record(best_pid)
            return MatchResult(
                person_id=best_pid,
                confidence=round(min(0.95, confidence * 0.88), 3),
                match_type="fuzzy_name",
                evidence=[
                    f"name_score={best_score:.0f}",
                    f"matched={best_name}",
                ],
            )

        # Score is in the review zone (80..91)
        self._queue_hygiene(
            action_type="potential_duplicate",
            person_a_id=best_pid,
            person_b_id=None,
            confidence=round(confidence * 0.85, 3),
            reason=(
                f"Fuzzy match: '{canonical_name}' ~ '{best_name}' "
                f"(score={best_score:.0f}, source={source_type})"
            ),
            proposed_data=json.dumps(claim, default=str),
        )
        return MatchResult(
            person_id=best_pid,
            confidence=round(confidence * 0.85, 3),
            match_type="review",
            evidence=[
                f"name_score={best_score:.0f}",
                f"queued_review={best_name}",
            ],
        )

    def _find_phonetic_candidates(
        self,
        pkey: str,
    ) -> list[tuple[str, str]]:
        """Find people whose canonical_name shares the same phonetic key.

        Returns a list of ``(person_id, canonical_name)`` tuples.
        """
        # Load all non-archived people (the people table is ~1k rows, so
        # a full scan with Python-side filtering is acceptable at this scale).
        rows = self._conn.execute(
            "SELECT id, canonical_name FROM people WHERE is_archived = 0"
        ).fetchall()

        candidates: list[tuple[str, str]] = []
        for row in rows:
            pid = row[0] if isinstance(row, tuple) else row["id"]
            cname = row[1] if isinstance(row, tuple) else row["canonical_name"]
            if not cname:
                continue
            if phonetic_key(cname) == pkey:
                candidates.append((pid, cname))

        return candidates

    def _count_shared_groups(
        self,
        person_id: str,
        wa_jids: list[str],
    ) -> int:
        """Count WhatsApp groups shared between a person and the claim's JIDs.

        The ``group_members`` table links people and JIDs to groups.  We find
        groups where *person_id* is a member AND at least one of the claim's
        JIDs is also a member.
        """
        if not wa_jids:
            return 0

        placeholders = ",".join("?" * len(wa_jids))
        row = self._conn.execute(
            f"""
            SELECT COUNT(DISTINCT gm1.group_id) AS cnt
            FROM group_members gm1
            JOIN group_members gm2 ON gm1.group_id = gm2.group_id
            WHERE gm1.person_id = ?
              AND gm2.wa_jid IN ({placeholders})
            """,
            [person_id] + wa_jids,
        ).fetchone()

        return row[0] if isinstance(row, tuple) else (row["cnt"] if row else 0)

    # ── Internal: create new person ──────────────────────────────────

    def _create_person(self, claim: dict[str, Any]) -> str:
        """Create a new person record from a claim.

        Inserts a row into the ``people`` table and a row into
        ``contact_metadata`` if the claim carries org/city/birthday data.

        Returns the new person_id.
        """
        now = _now_epoch()
        person_id = _nanoid("p_")

        raw_name = claim.get("name", "")
        if not raw_name:
            fn = claim.get("first_name", "")
            ln = claim.get("last_name", "")
            raw_name = f"{fn} {ln}".strip()

        canonical, first, last = normalize_name(raw_name) if raw_name else ("", "", "")

        # If we still have no usable name, derive one from identifiers.
        if not canonical:
            if claim.get("phones"):
                canonical = claim["phones"][0]
            elif claim.get("emails"):
                canonical = claim["emails"][0]
            elif claim.get("wa_jids"):
                canonical = claim["wa_jids"][0]
            else:
                canonical = f"Unknown ({claim.get('source_type', 'claim')})"

        self._conn.execute(
            """INSERT INTO people
               (id, canonical_name, first_name, last_name, importance,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (person_id, canonical, first, last, 4, now, now),
        )

        # Metadata
        org = claim.get("organization")
        city = claim.get("city")
        birthday = claim.get("birthday")
        job_title = claim.get("job_title")
        country = claim.get("country")

        if any([org, city, birthday, job_title, country]):
            self._conn.execute(
                """INSERT OR IGNORE INTO contact_metadata
                   (person_id, organization, job_title, city, country, birthday)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (person_id, org, job_title, city, country, birthday),
            )

        self._conn.commit()
        log.info("Created person %s: %s", person_id, canonical)
        return person_id

    # ── Internal: insert claim data ──────────────────────────────────

    def _insert_claim_data(
        self,
        person_id: str,
        claim: dict[str, Any],
    ) -> str | None:
        """Persist the claim's identifiers and raw data.

        When the ontology tables exist, writes to ``source_record`` and
        ``contact_point``.  Always writes to ``person_identifiers`` as well
        for backward compatibility with the existing resolver and adapters.

        Returns the source_record ID (or None if ontology tables are absent).
        """
        now = _now_epoch()
        source_type = claim.get("source_type", "unknown")
        source_id = claim.get("source_id")
        priority = SOURCE_PRIORITY.get(source_type, 10)
        sr_id: str | None = None

        # ── Ontology tables (source_record + contact_point) ─────────
        if self._has_ontology:
            sr_id = _nanoid("sr_")
            raw = claim.get("raw_data")
            self._conn.execute(
                """INSERT INTO source_record
                   (id, person_id, source_type, source_id, raw_data,
                    synced_at, priority, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sr_id,
                    person_id,
                    source_type,
                    source_id,
                    json.dumps(raw, default=str) if raw else None,
                    now,
                    priority,
                    now,
                ),
            )

            # Insert contact points
            for phone in claim.get("phones", []):
                norm = normalize_phone(phone)
                if norm:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO contact_point
                           (id, person_id, type, value, normalized, source_id,
                            created_at)
                           VALUES (?, ?, 'phone', ?, ?, ?, ?)""",
                        (_nanoid("cp_"), person_id, phone, norm, sr_id, now),
                    )

            for email in claim.get("emails", []):
                norm = normalize_email(email)
                if norm:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO contact_point
                           (id, person_id, type, value, normalized, source_id,
                            created_at)
                           VALUES (?, ?, 'email', ?, ?, ?, ?)""",
                        (_nanoid("cp_"), person_id, email, norm, sr_id, now),
                    )

            for jid in claim.get("wa_jids", []):
                jid = jid.strip()
                if jid:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO contact_point
                           (id, person_id, type, value, normalized, source_id,
                            created_at)
                           VALUES (?, ?, 'wa_jid', ?, ?, ?, ?)""",
                        (_nanoid("cp_"), person_id, jid, jid, sr_id, now),
                    )

        # ── Legacy table (person_identifiers) — always write ────────
        for phone in claim.get("phones", []):
            norm = normalize_phone(phone)
            digits = norm.lstrip("+") if norm else ""
            if digits:
                self._conn.execute(
                    """INSERT OR IGNORE INTO person_identifiers
                       (person_id, type, value, normalized, source, added_at)
                       VALUES (?, 'phone', ?, ?, ?, ?)""",
                    (person_id, phone, digits, source_type, now),
                )

        for email in claim.get("emails", []):
            norm = normalize_email(email)
            if norm:
                self._conn.execute(
                    """INSERT OR IGNORE INTO person_identifiers
                       (person_id, type, value, normalized, source, added_at)
                       VALUES (?, 'email', ?, ?, ?, ?)""",
                    (person_id, email, norm, source_type, now),
                )

        for jid in claim.get("wa_jids", []):
            jid = jid.strip()
            if jid:
                self._conn.execute(
                    """INSERT OR IGNORE INTO person_identifiers
                       (person_id, type, value, normalized, source, added_at)
                       VALUES (?, 'wa_jid', ?, ?, ?, ?)""",
                    (person_id, jid, jid, source_type, now),
                )

        self._conn.commit()
        return sr_id

    # ── Internal: golden record from source_records ──────────────────

    def _build_golden_from_source_records(
        self,
        person_id: str,
        now: int,
    ) -> None:
        """Rebuild the golden record using source_record rows.

        For each field, the value from the highest-priority source wins.
        """
        rows = self._conn.execute(
            "SELECT source_type, raw_data, priority "
            "FROM source_record WHERE person_id = ? "
            "ORDER BY priority DESC, synced_at DESC",
            (person_id,),
        ).fetchall()

        if not rows:
            # No source records yet — nothing to rebuild.
            return

        # Accumulate best values per field
        best: dict[str, tuple[Any, int]] = {}  # field -> (value, priority)

        for row in rows:
            src_type = row[0] if isinstance(row, tuple) else row["source_type"]
            raw_str = row[1] if isinstance(row, tuple) else row["raw_data"]
            prio = row[2] if isinstance(row, tuple) else row["priority"]

            if not raw_str:
                continue
            try:
                data = json.loads(raw_str)
            except (json.JSONDecodeError, TypeError):
                continue

            for field_name in (
                "name", "first_name", "last_name",
                "organization", "job_title", "city", "country", "birthday",
            ):
                val = data.get(field_name)
                if val and (
                    field_name not in best or prio > best[field_name][1]
                ):
                    best[field_name] = (val, prio)

        # Apply to people table
        updates: dict[str, Any] = {"updated_at": now}

        if "name" in best:
            canonical, first, last = normalize_name(best["name"][0])
            if canonical:
                updates["canonical_name"] = canonical
                updates["first_name"] = first
                updates["last_name"] = last
        else:
            if "first_name" in best:
                updates["first_name"] = best["first_name"][0]
            if "last_name" in best:
                updates["last_name"] = best["last_name"][0]
            if "first_name" in best or "last_name" in best:
                fn = updates.get("first_name") or ""
                ln = updates.get("last_name") or ""
                updates["canonical_name"] = f"{fn} {ln}".strip()

        # Mark golden record timestamp
        if _table_exists(self._conn, "people"):
            # Check if golden_record_at column exists
            cols = [
                r[1] for r in
                self._conn.execute("PRAGMA table_info(people)").fetchall()
            ]
            if "golden_record_at" in cols:
                updates["golden_record_at"] = now

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [person_id]
            self._conn.execute(
                f"UPDATE people SET {set_clause} WHERE id = ?", vals
            )

        # Apply to contact_metadata
        meta_fields: dict[str, Any] = {}
        for field_name, col_name in [
            ("organization", "organization"),
            ("job_title", "job_title"),
            ("city", "city"),
            ("country", "country"),
            ("birthday", "birthday"),
        ]:
            if field_name in best:
                meta_fields[col_name] = best[field_name][0]

        if meta_fields:
            existing = self._conn.execute(
                "SELECT person_id FROM contact_metadata WHERE person_id = ?",
                (person_id,),
            ).fetchone()

            if existing:
                set_clause = ", ".join(f"{k} = ?" for k in meta_fields)
                vals = list(meta_fields.values()) + [person_id]
                self._conn.execute(
                    f"UPDATE contact_metadata SET {set_clause} WHERE person_id = ?",
                    vals,
                )
            else:
                meta_fields["person_id"] = person_id
                cols = ", ".join(meta_fields.keys())
                placeholders = ", ".join("?" for _ in meta_fields)
                self._conn.execute(
                    f"INSERT INTO contact_metadata ({cols}) VALUES ({placeholders})",
                    list(meta_fields.values()),
                )

        self._conn.commit()

    # ── Internal: hygiene queue ──────────────────────────────────────

    def _queue_hygiene(
        self,
        action_type: str,
        person_a_id: str | None,
        person_b_id: str | None,
        confidence: float,
        reason: str,
        proposed_data: str | None = None,
    ) -> None:
        """Insert an item into the hygiene_queue for human review.

        Silently skips if the ``hygiene_queue`` table does not exist (pre-
        migration 027).
        """
        if not _table_exists(self._conn, "hygiene_queue"):
            log.warning(
                "hygiene_queue table missing; skipping queue for: %s", reason
            )
            return

        now = _now_epoch()
        self._conn.execute(
            """INSERT INTO hygiene_queue
               (id, action_type, person_a_id, person_b_id, confidence,
                reason, proposed_data, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                _nanoid("hq_"),
                action_type,
                person_a_id,
                person_b_id,
                confidence,
                reason,
                proposed_data,
                now,
            ),
        )
        self._conn.commit()
        log.info("Queued hygiene item: %s (%.2f) %s", action_type, confidence, reason)
