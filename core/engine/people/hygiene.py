"""Contact Hygiene Engine.

Detects data quality issues and provides auto-fix + operator review workflows.
Tier 1 fixes run automatically (normalization, dedup contact points).
Tier 2 issues go to the hygiene_queue for operator review (merges, archives).
"""

from __future__ import annotations

import re
import sqlite3
import string
import time
import unicodedata
from pathlib import Path
from random import choices
from typing import Any

from rapidfuzz import fuzz

from .normalize import (
    double_metaphone_key,
    normalize_email,
    normalize_name,
    normalize_phone,
    phonetic_key,
)

# ---------------------------------------------------------------------------
# Name splitting (Phase 6.1 — canonical name hygiene)
# ---------------------------------------------------------------------------

# Allowed characters in a "splittable" Latin name: letters, dot, hyphen, apostrophe.
_SPLIT_ALLOWED_RE = re.compile(r"^[A-Za-z.\-']+$")
# Run of 4+ consecutive uppercase letters (e.g. MICHIGAN, BRADFORDUK) — likely a tag, not a name.
_ALLCAPS_RUN_RE = re.compile(r"[A-Z]{4,}")
# Lowercase→Uppercase boundary.
_LC_UC_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
# Dot followed by a letter (no space): "Dr.Badar" → "Dr. Badar".
_DOT_LETTER_RE = re.compile(r"\.(?=[A-Za-z])")


def _smart_title_token(token: str) -> str:
    """Title-case a single ASCII token, leaving any non-ASCII unchanged."""
    try:
        token.encode("ascii")
        return token.title()
    except UnicodeEncodeError:
        return token


def split_concatenated_name(raw: str | None) -> str | None:
    """Try to split a CamelCase / dot-joined Latin name into spaced form.

    Returns the cleaned name (e.g. ``"Abdus Samad Rashid"``) if the input is
    safely splittable, otherwise ``None``. Never raises.

    Acceptance rules (all must hold, else returns None):
      * Input is a non-empty string of length 4–60
      * No existing whitespace in input
      * No ``/`` (compound entries go to the review queue)
      * ASCII-only — letters plus ``.``, ``-``, ``'``
      * No run of 4+ consecutive uppercase letters (location tags like
        ``MICHIGAN`` are not names)
      * After splitting: ≥ 2 tokens, every token 2–20 chars, total ≤ 60 chars
      * Result must differ from the input

    Examples:
        >>> split_concatenated_name("AbdusSamadRashid")
        'Abdus Samad Rashid'
        >>> split_concatenated_name("Dr.BadarUlIslam")
        'Dr. Badar Ul Islam'
        >>> split_concatenated_name("KhaleeqUrRehman")
        'Khaleeq Ur Rehman'
        >>> split_concatenated_name("Ahmed") is None
        True
        >>> split_concatenated_name("Ahmed Ali") is None
        True
        >>> split_concatenated_name("AyeshaCOUSIN/MICHIGAN") is None
        True
    """
    if not raw or not isinstance(raw, str):
        return None
    if any(ch.isspace() for ch in raw):
        return None
    if "/" in raw:
        return None
    if not (4 <= len(raw) <= 60):
        return None
    if not _SPLIT_ALLOWED_RE.match(raw):
        return None
    if _ALLCAPS_RUN_RE.search(raw):
        return None

    # Insert space at lowercase→Uppercase boundaries.
    spaced = _LC_UC_BOUNDARY_RE.sub(" ", raw)
    # Insert space after dot before a letter ("Dr.Badar" → "Dr. Badar").
    spaced = _DOT_LETTER_RE.sub(". ", spaced)
    # Collapse any accidental double spaces.
    spaced = re.sub(r"\s+", " ", spaced).strip()

    tokens = spaced.split(" ")
    if len(tokens) < 2:
        return None
    for tok in tokens:
        if not (2 <= len(tok) <= 20):
            return None

    cleaned = " ".join(_smart_title_token(t) for t in tokens)
    if len(cleaned) > 60 or cleaned == raw:
        return None
    return cleaned


# Arabic Unicode range — used by scan_dirty_names() to flag concatenated RTL names.
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000200D"             # ZWJ
    "\U0000FE0F"             # VS16
    "]+",
    flags=re.UNICODE,
)

_ID_CHARS = string.ascii_lowercase + string.digits


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


# ---------------------------------------------------------------------------
# HygieneEngine
# ---------------------------------------------------------------------------


class HygieneEngine:
    """Detect and resolve data quality issues in the people database."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._conn = _connect(conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ------------------------------------------------------------------
    # Scanners
    # ------------------------------------------------------------------

    def scan_duplicates(self) -> list[dict[str, Any]]:
        """Find people sharing the same normalized phone/email or very similar names."""
        issues: list[dict[str, Any]] = []

        # --- Shared contact point (phone or email) ---
        if _has_table(self.conn, "contact_point"):
            rows = self.conn.execute("""
                SELECT cp.type, cp.normalized, GROUP_CONCAT(cp.person_id) AS pids
                FROM contact_point cp
                JOIN people p ON cp.person_id = p.id
                WHERE p.is_archived = 0
                  AND cp.normalized IS NOT NULL
                  AND cp.type IN ('phone', 'email')
                GROUP BY cp.type, cp.normalized
                HAVING COUNT(DISTINCT cp.person_id) > 1
            """).fetchall()

            for row in rows:
                pids = row["pids"].split(",")
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        issues.append({
                            "action_type": "merge",
                            "person_a_id": pids[i],
                            "person_b_id": pids[j],
                            "confidence": 0.95,
                            "reason": f"Shared {row['type']}: {row['normalized']}",
                        })

        # Also check person_identifiers for shared normalized values
        rows_pi = self.conn.execute("""
            SELECT pi.type, pi.normalized, GROUP_CONCAT(pi.person_id) AS pids
            FROM person_identifiers pi
            JOIN people p ON pi.person_id = p.id
            WHERE p.is_archived = 0
              AND pi.normalized IS NOT NULL
              AND pi.type IN ('phone', 'email')
            GROUP BY pi.type, pi.normalized
            HAVING COUNT(DISTINCT pi.person_id) > 1
        """).fetchall()

        seen_pairs: set[tuple[str, str]] = set()
        for issue in issues:
            pair = tuple(sorted([issue["person_a_id"], issue["person_b_id"]]))
            seen_pairs.add(pair)

        for row in rows_pi:
            pids = row["pids"].split(",")
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    pair = tuple(sorted([pids[i], pids[j]]))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        issues.append({
                            "action_type": "merge",
                            "person_a_id": pids[i],
                            "person_b_id": pids[j],
                            "confidence": 0.90,
                            "reason": f"Shared {row['type']} (identifiers): {row['normalized']}",
                        })

        # --- Similar canonical_name (fuzzy match) ---
        people = self.conn.execute(
            "SELECT id, canonical_name FROM people WHERE is_archived = 0 AND canonical_name IS NOT NULL"
        ).fetchall()

        name_list = [(p["id"], p["canonical_name"]) for p in people]
        for i in range(len(name_list)):
            for j in range(i + 1, len(name_list)):
                pid_a, name_a = name_list[i]
                pid_b, name_b = name_list[j]
                pair = tuple(sorted([pid_a, pid_b]))
                if pair in seen_pairs:
                    continue
                score = fuzz.token_sort_ratio(name_a, name_b)
                if score >= 85:
                    seen_pairs.add(pair)
                    issues.append({
                        "action_type": "merge",
                        "person_a_id": pid_a,
                        "person_b_id": pid_b,
                        "confidence": round(score / 100.0, 2),
                        "reason": f"Similar names ({score}%): '{name_a}' vs '{name_b}'",
                    })

        return issues

    def batch_dedup(self) -> dict[str, int]:
        """Run a comprehensive dedup pass using phonetic blocking + identifier matching.

        Uses Double Metaphone and Arabic phonetic groups as blocking keys,
        then scores candidate pairs with identifier overlap + name similarity.
        Results are inserted into the hygiene_queue for review or auto-merge.

        Returns {candidates: N, auto_merged: N, queued_for_review: N}.
        """
        stats = {"candidates": 0, "auto_merged": 0, "queued_for_review": 0}
        now = _now()

        # Build person index: id -> {name, phonetic, metaphone, phones, emails, wa_jids}
        people = self.conn.execute(
            "SELECT id, canonical_name FROM people WHERE is_archived = 0 AND is_self = 0"
        ).fetchall()

        index: dict[str, dict] = {}
        for p in people:
            pid, name = p["id"], p["canonical_name"]
            index[pid] = {
                "name": name,
                "phonetic": phonetic_key(name),
                "metaphone": double_metaphone_key(name),
                "phones": set(),
                "emails": set(),
                "wa_jids": set(),
            }

        # Load identifiers
        idents = self.conn.execute(
            "SELECT person_id, type, value FROM person_identifiers"
        ).fetchall()
        for ident in idents:
            pid = ident["person_id"]
            if pid not in index:
                continue
            val = (ident["value"] or "").strip().lower()
            if ident["type"] == "phone":
                # Normalize to last 8 digits for matching
                digits = re.sub(r"[^\d]", "", val)
                if len(digits) >= 8:
                    index[pid]["phones"].add(digits[-8:])
            elif ident["type"] == "email":
                index[pid]["emails"].add(val)
            elif ident["type"] == "wa_jid":
                index[pid]["wa_jids"].add(val)

        # Build blocking groups: metaphone key -> set of person_ids
        blocks: dict[str, set[str]] = {}
        for pid, data in index.items():
            for key in (data["phonetic"], data["metaphone"]):
                if key and len(key) > 2:
                    blocks.setdefault(key, set()).add(pid)

        # Score candidate pairs within each block
        seen_pairs: set[tuple[str, str]] = set()
        candidates: list[dict] = []

        for _block_key, pid_set in blocks.items():
            pids = sorted(pid_set)
            if len(pids) < 2 or len(pids) > 50:
                continue  # Skip huge blocks (common names like "ahmad")

            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    pair = tuple(sorted([pids[i], pids[j]]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    a, b = index[pids[i]], index[pids[j]]
                    score = 0.0
                    reasons = []

                    # Shared phone (strongest signal)
                    shared_phones = a["phones"] & b["phones"]
                    if shared_phones:
                        score += 0.50
                        reasons.append(f"shared phone suffix: {list(shared_phones)[0]}")

                    # Shared email
                    shared_emails = a["emails"] & b["emails"]
                    if shared_emails:
                        score += 0.45
                        reasons.append(f"shared email: {list(shared_emails)[0]}")

                    # Shared WhatsApp JID
                    shared_wa = a["wa_jids"] & b["wa_jids"]
                    if shared_wa:
                        score += 0.45
                        reasons.append(f"shared wa_jid: {list(shared_wa)[0]}")

                    # Name similarity (only if first names also match)
                    name_score = fuzz.token_sort_ratio(a["name"], b["name"])
                    if name_score >= 90:
                        # Check first names specifically to avoid "Saad Tariq" vs "Sana Tariq"
                        a_first = a["name"].split()[0] if a["name"] else ""
                        b_first = b["name"].split()[0] if b["name"] else ""
                        first_score = fuzz.ratio(a_first, b_first)
                        if first_score >= 80:
                            score += 0.30 * (name_score / 100.0)
                            reasons.append(f"name match: {name_score}%")
                        else:
                            # Same last name, different first name — not a match
                            score += 0.05
                            reasons.append(f"same surname only: {name_score}%")

                    if score >= 0.50:
                        candidates.append({
                            "person_a_id": pair[0],
                            "person_b_id": pair[1],
                            "confidence": min(score, 1.0),
                            "reason": "; ".join(reasons),
                        })

        stats["candidates"] = len(candidates)

        # Process candidates
        for c in candidates:
            has_shared_id = any(
                "shared phone" in c["reason"] or
                "shared email" in c["reason"] or
                "shared wa_jid" in c["reason"]
                for _ in [1]
            )

            if c["confidence"] >= 0.90 and has_shared_id:
                # Auto-merge
                try:
                    self.merge(c["person_a_id"], c["person_b_id"])
                    self.conn.commit()
                    stats["auto_merged"] += 1
                except Exception as e:
                    logger.warning("batch_dedup auto-merge failed: %s", e)
            elif c["confidence"] >= 0.50:
                # Queue for review
                qid = _gen_id("hq")
                try:
                    self.conn.execute(
                        """INSERT OR IGNORE INTO hygiene_queue
                           (id, action_type, person_a_id, person_b_id, confidence, reason, status, created_at)
                           VALUES (?, 'merge', ?, ?, ?, ?, 'pending', ?)""",
                        (qid, c["person_a_id"], c["person_b_id"],
                         round(c["confidence"], 2), c["reason"], now),
                    )
                    stats["queued_for_review"] += 1
                except sqlite3.IntegrityError:
                    pass  # Already queued

        self.conn.commit()
        return stats

    def scan_ghosts(self) -> list[dict[str, Any]]:
        """Find people that are likely junk imports: no interactions, no metadata, single identifier."""
        cutoff = _now() - (365 * 86400)
        rows = self.conn.execute("""
            SELECT p.id
            FROM people p
            WHERE p.is_archived = 0
              AND NOT EXISTS (
                  SELECT 1 FROM interactions i
                  WHERE i.person_id = p.id AND i.occurred_at > ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM contact_metadata cm
                  WHERE cm.person_id = p.id
                    AND (cm.organization IS NOT NULL
                         OR cm.birthday IS NOT NULL
                         OR cm.city IS NOT NULL)
              )
              AND (
                  SELECT COUNT(*) FROM person_identifiers pi
                  WHERE pi.person_id = p.id
              ) <= 1
        """, (cutoff,)).fetchall()

        issues = []
        for row in rows:
            issues.append({
                "action_type": "archive_ghost",
                "person_a_id": row["id"],
                "person_b_id": None,
                "confidence": 0.80,
                "reason": "Ghost: no interactions in 365d, no metadata, single identifier",
            })
        return issues

    def scan_incomplete(self) -> list[dict[str, Any]]:
        """Find people missing both phone and email."""
        has_cp = _has_table(self.conn, "contact_point")
        if has_cp:
            query = """
                SELECT p.id, p.canonical_name
                FROM people p
                WHERE p.is_archived = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM contact_point cp
                      WHERE cp.person_id = p.id AND cp.type IN ('phone', 'email')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM person_identifiers pi
                      WHERE pi.person_id = p.id AND pi.type IN ('phone', 'email')
                  )
            """
        else:
            query = """
                SELECT p.id, p.canonical_name
                FROM people p
                WHERE p.is_archived = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM person_identifiers pi
                      WHERE pi.person_id = p.id AND pi.type IN ('phone', 'email')
                  )
            """
        rows = self.conn.execute(query).fetchall()

        issues = []
        for row in rows:
            issues.append({
                "action_type": "incomplete",
                "person_a_id": row["id"],
                "person_b_id": None,
                "confidence": 1.0,
                "reason": f"Missing phone and email: {row['canonical_name']}",
            })
        return issues

    def scan_stale(self) -> list[dict[str, Any]]:
        """Find non-archived people with no interactions in 365 days and low importance."""
        cutoff = _now() - (365 * 86400)
        rows = self.conn.execute("""
            SELECT p.id, p.canonical_name, p.importance
            FROM people p
            WHERE p.is_archived = 0
              AND p.importance >= 3
              AND NOT EXISTS (
                  SELECT 1 FROM interactions i
                  WHERE i.person_id = p.id AND i.occurred_at > ?
              )
        """, (cutoff,)).fetchall()

        issues = []
        for row in rows:
            issues.append({
                "action_type": "archive_stale",
                "person_a_id": row["id"],
                "person_b_id": None,
                "confidence": 0.70,
                "reason": f"Stale: '{row['canonical_name']}' importance={row['importance']}, no interaction in 365d",
            })
        return issues

    def scan_dirty_names(self) -> list[dict[str, Any]]:
        """Find canonical names that look concatenated but the safe splitter rejected.

        Detects four sub-cases (encoded in the reason text):
          * ``slash_compound`` — contains ``/`` (likely two people or a tag)
          * ``allcaps_tag``    — run of 4+ uppercase letters (location/org tag)
          * ``arabic_concat``  — Arabic-script name with no spaces, length > 8
          * ``split_rejected`` — would-be CamelCase split, but a token failed
                                  the size constraints (> 20 or < 2 chars)
        """
        rows = self.conn.execute(
            "SELECT id, canonical_name FROM people "
            "WHERE is_archived = 0 AND canonical_name IS NOT NULL"
        ).fetchall()

        issues: list[dict[str, Any]] = []
        for row in rows:
            cn = row["canonical_name"]
            if not cn:
                continue

            sub: str | None = None

            if "/" in cn:
                sub = "slash_compound"
            elif _ALLCAPS_RUN_RE.search(cn):
                sub = "allcaps_tag"
            elif _ARABIC_RE.search(cn) and " " not in cn and len(cn) > 8:
                sub = "arabic_concat"
            else:
                # CamelCase candidate that the splitter rejected on token size?
                if (
                    len(cn) >= 4
                    and " " not in cn
                    and _SPLIT_ALLOWED_RE.match(cn)
                    and _LC_UC_BOUNDARY_RE.search(cn)
                    and split_concatenated_name(cn) is None
                ):
                    sub = "split_rejected"

            if sub is None:
                continue

            issues.append({
                "action_type": "rename_review",
                "person_a_id": row["id"],
                "person_b_id": None,
                "confidence": 1.0,
                "reason": f"{sub}: '{cn}'",
            })
        return issues

    def scan_all(self) -> dict[str, int]:
        """Run all scanners and insert results into hygiene_queue.

        Skips insertion if an identical pending issue already exists.
        Returns counts per action_type.
        """
        all_issues: list[dict[str, Any]] = []
        all_issues.extend(self.scan_duplicates())
        all_issues.extend(self.scan_ghosts())
        all_issues.extend(self.scan_incomplete())
        all_issues.extend(self.scan_stale())
        all_issues.extend(self.scan_dirty_names())

        counts: dict[str, int] = {}

        if not all_issues:
            return counts

        # hygiene_queue may not exist yet (migration 027 not applied)
        if not _has_table(self.conn, "hygiene_queue"):
            for issue in all_issues:
                counts[issue["action_type"]] = counts.get(issue["action_type"], 0) + 1
            return counts

        now = _now()

        for issue in all_issues:
            action_type = issue["action_type"]
            person_a = issue["person_a_id"]
            person_b = issue.get("person_b_id")

            # Check for existing pending issue with same type + people
            existing = self.conn.execute(
                """
                SELECT 1 FROM hygiene_queue
                WHERE action_type = ?
                  AND person_a_id IS ?
                  AND person_b_id IS ?
                  AND status = 'pending'
                LIMIT 1
                """,
                (action_type, person_a, person_b),
            ).fetchone()

            if existing:
                continue

            queue_id = _gen_id("hq")
            self.conn.execute(
                """
                INSERT INTO hygiene_queue (id, action_type, person_a_id, person_b_id,
                                           confidence, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (queue_id, action_type, person_a, person_b,
                 issue["confidence"], issue["reason"], now),
            )
            counts[action_type] = counts.get(action_type, 0) + 1

        self.conn.commit()
        return counts

    # ------------------------------------------------------------------
    # Tier 1 Auto-fixes
    # ------------------------------------------------------------------

    def run_tier1_fixes(self) -> dict[str, int]:
        """Run safe auto-fixes that don't require operator approval.

        Returns a dict with counts per fix type.
        """
        counts: dict[str, int] = {
            "phones_normalized": 0,
            "contact_points_deduped": 0,
            "status_jids_removed": 0,
            "names_trimmed": 0,
            "unicode_normalized": 0,
            "emoji_stripped": 0,
            "names_split": 0,
        }

        # --- Normalize phones to E.164 in person_identifiers ---
        phone_rows = self.conn.execute(
            "SELECT rowid, person_id, type, value, normalized FROM person_identifiers WHERE type = 'phone'"
        ).fetchall()
        for row in phone_rows:
            try:
                e164 = normalize_phone(row["value"])
            except Exception:
                continue
            if e164 and e164 != row["normalized"]:
                self.conn.execute(
                    "UPDATE person_identifiers SET normalized = ? WHERE person_id = ? AND type = ? AND value = ?",
                    (e164, row["person_id"], row["type"], row["value"]),
                )
                counts["phones_normalized"] += 1

        # --- Deduplicate identical contact_point entries ---
        if _has_table(self.conn, "contact_point"):
            dup_cps = self.conn.execute("""
                SELECT person_id, type, normalized, COUNT(*) AS cnt, MIN(id) AS keep_id
                FROM contact_point
                WHERE normalized IS NOT NULL
                GROUP BY person_id, type, normalized
                HAVING cnt > 1
            """).fetchall()
            for dup in dup_cps:
                deleted = self.conn.execute(
                    "DELETE FROM contact_point WHERE person_id = ? AND type = ? AND normalized = ? AND id != ?",
                    (dup["person_id"], dup["type"], dup["normalized"], dup["keep_id"]),
                ).rowcount
                counts["contact_points_deduped"] += deleted

        # --- Remove @status WhatsApp JIDs ---
        removed = self.conn.execute(
            "DELETE FROM person_identifiers WHERE value LIKE '%@status'"
        ).rowcount
        counts["status_jids_removed"] += removed

        # Also from contact_point
        if _has_table(self.conn, "contact_point"):
            removed_cp = self.conn.execute(
                "DELETE FROM contact_point WHERE value LIKE '%@status'"
            ).rowcount
            counts["status_jids_removed"] += removed_cp

        # --- Trim names and normalize Unicode ---
        people = self.conn.execute(
            "SELECT id, canonical_name, display_name, first_name, last_name FROM people WHERE is_archived = 0"
        ).fetchall()

        now = _now()
        for person in people:
            updates: dict[str, str] = {}
            pid = person["id"]

            for col in ("canonical_name", "display_name", "first_name", "last_name"):
                val = person[col]
                if val is None:
                    continue

                # Trim whitespace
                trimmed = val.strip()
                if trimmed != val:
                    updates[col] = trimmed
                    counts["names_trimmed"] += 1
                    val = trimmed

                # NFC normalization
                nfc = unicodedata.normalize("NFC", val)
                if nfc != val:
                    updates[col] = nfc
                    counts["unicode_normalized"] += 1
                    val = nfc

            # Strip emoji from canonical_name specifically
            cn = updates.get("canonical_name", person["canonical_name"])
            if cn:
                stripped = _EMOJI_RE.sub("", cn).strip()
                # Collapse multiple spaces left by emoji removal
                stripped = re.sub(r"\s{2,}", " ", stripped)
                if stripped != cn and stripped:
                    updates["canonical_name"] = stripped
                    counts["emoji_stripped"] += 1

            # Split CamelCase / dot-joined Latin names ("AbdusSamadRashid" →
            # "Abdus Samad Rashid"). Conservative — see split_concatenated_name.
            cn_for_split = updates.get("canonical_name", person["canonical_name"])
            split = split_concatenated_name(cn_for_split)
            if split:
                old_cn = person["canonical_name"]
                updates["canonical_name"] = split
                parts = split.split(" ")
                updates["first_name"] = parts[0]
                updates["last_name"] = " ".join(parts[1:])
                # Only overwrite display_name if it tracks the old canonical form
                if person["display_name"] == old_cn:
                    updates["display_name"] = split
                # Preserve the original concatenated form as a lookup alias
                if old_cn:
                    alias_val = old_cn.lower().strip()
                    try:
                        self.conn.execute(
                            "INSERT INTO aliases (alias, person_id, type, priority, created_at) "
                            "VALUES (?, ?, 'pre_split', 0, ?)",
                            (alias_val, pid, now),
                        )
                    except sqlite3.IntegrityError:
                        pass  # alias already taken (collision or rerun)
                counts["names_split"] += 1

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [now, pid]
                self.conn.execute(
                    f"UPDATE people SET {set_clause}, updated_at = ? WHERE id = ?",
                    vals,
                )

        self.conn.commit()
        return counts

    # ------------------------------------------------------------------
    # Merge Engine
    # ------------------------------------------------------------------

    def merge(self, primary_id: str, secondary_id: str) -> dict[str, Any]:
        """Merge secondary person into primary.

        Moves all related records, archives the secondary, and logs the action.
        Returns a summary dict with counts of moved records.
        """
        summary: dict[str, int] = {
            "contact_points_moved": 0,
            "interactions_moved": 0,
            "relationships_moved": 0,
            "group_members_moved": 0,
            "aliases_moved": 0,
        }
        now = _now()

        # 1. Move contact_point rows (handle UNIQUE conflicts)
        if _has_table(self.conn, "contact_point"):
            cps = self.conn.execute(
                "SELECT id, type, normalized FROM contact_point WHERE person_id = ?",
                (secondary_id,),
            ).fetchall()
            for cp in cps:
                try:
                    self.conn.execute(
                        "UPDATE contact_point SET person_id = ? WHERE id = ?",
                        (primary_id, cp["id"]),
                    )
                    summary["contact_points_moved"] += 1
                except sqlite3.IntegrityError:
                    # Duplicate — remove the secondary's copy
                    self.conn.execute("DELETE FROM contact_point WHERE id = ?", (cp["id"],))

        # Also move person_identifiers
        idents = self.conn.execute(
            "SELECT type, value FROM person_identifiers WHERE person_id = ?",
            (secondary_id,),
        ).fetchall()
        for ident in idents:
            try:
                self.conn.execute(
                    "UPDATE person_identifiers SET person_id = ? WHERE person_id = ? AND type = ? AND value = ?",
                    (primary_id, secondary_id, ident["type"], ident["value"]),
                )
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "DELETE FROM person_identifiers WHERE person_id = ? AND type = ? AND value = ?",
                    (secondary_id, ident["type"], ident["value"]),
                )

        # 2. Move interactions
        summary["interactions_moved"] = self.conn.execute(
            "UPDATE interactions SET person_id = ? WHERE person_id = ?",
            (primary_id, secondary_id),
        ).rowcount

        # 3. Move relationships
        moved_a = self.conn.execute(
            "UPDATE OR IGNORE relationships SET person_a_id = ? WHERE person_a_id = ?",
            (primary_id, secondary_id),
        ).rowcount
        moved_b = self.conn.execute(
            "UPDATE OR IGNORE relationships SET person_b_id = ? WHERE person_b_id = ?",
            (primary_id, secondary_id),
        ).rowcount
        # Clean up any that couldn't be moved (duplicates)
        self.conn.execute(
            "DELETE FROM relationships WHERE person_a_id = ? OR person_b_id = ?",
            (secondary_id, secondary_id),
        )
        summary["relationships_moved"] = moved_a + moved_b

        # 4. Move group_members
        gms = self.conn.execute(
            "SELECT group_id, wa_jid FROM group_members WHERE person_id = ?",
            (secondary_id,),
        ).fetchall()
        for gm in gms:
            try:
                self.conn.execute(
                    "UPDATE group_members SET person_id = ? WHERE person_id = ? AND group_id = ? AND wa_jid IS ?",
                    (primary_id, secondary_id, gm["group_id"], gm["wa_jid"]),
                )
                summary["group_members_moved"] += 1
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "DELETE FROM group_members WHERE person_id = ? AND group_id = ? AND wa_jid IS ?",
                    (secondary_id, gm["group_id"], gm["wa_jid"]),
                )

        # 5. Merge relationship_state (keep higher counts from both)
        primary_rs = self.conn.execute(
            "SELECT * FROM relationship_state WHERE person_id = ?", (primary_id,)
        ).fetchone()
        secondary_rs = self.conn.execute(
            "SELECT * FROM relationship_state WHERE person_id = ?", (secondary_id,)
        ).fetchone()

        if secondary_rs:
            if primary_rs:
                # Merge: keep higher counts, more recent interaction
                merge_fields = {
                    "interaction_count_7d": max(
                        primary_rs["interaction_count_7d"] or 0,
                        secondary_rs["interaction_count_7d"] or 0,
                    ),
                    "interaction_count_30d": max(
                        primary_rs["interaction_count_30d"] or 0,
                        secondary_rs["interaction_count_30d"] or 0,
                    ),
                    "interaction_count_90d": max(
                        primary_rs["interaction_count_90d"] or 0,
                        secondary_rs["interaction_count_90d"] or 0,
                    ),
                    "msg_count_30d": max(
                        primary_rs["msg_count_30d"] or 0,
                        secondary_rs["msg_count_30d"] or 0,
                    ),
                    "outbound_30d": max(
                        primary_rs["outbound_30d"] or 0,
                        secondary_rs["outbound_30d"] or 0,
                    ),
                    "inbound_30d": max(
                        primary_rs["inbound_30d"] or 0,
                        secondary_rs["inbound_30d"] or 0,
                    ),
                    "last_interaction_at": max(
                        primary_rs["last_interaction_at"] or 0,
                        secondary_rs["last_interaction_at"] or 0,
                    ) or None,
                }
                set_clause = ", ".join(f"{k} = ?" for k in merge_fields)
                self.conn.execute(
                    f"UPDATE relationship_state SET {set_clause}, computed_at = ? WHERE person_id = ?",
                    list(merge_fields.values()) + [now, primary_id],
                )
            else:
                # Primary has no state — re-assign secondary's row
                self.conn.execute(
                    "UPDATE relationship_state SET person_id = ? WHERE person_id = ?",
                    (primary_id, secondary_id),
                )
            # Remove secondary's row if it still exists
            self.conn.execute(
                "DELETE FROM relationship_state WHERE person_id = ?", (secondary_id,)
            )

        # 6. Merge contact_metadata (field-level: prefer non-null, prefer primary on conflict)
        primary_cm = self.conn.execute(
            "SELECT * FROM contact_metadata WHERE person_id = ?", (primary_id,)
        ).fetchone()
        secondary_cm = self.conn.execute(
            "SELECT * FROM contact_metadata WHERE person_id = ?", (secondary_id,)
        ).fetchone()

        if secondary_cm:
            merge_cols = [
                "birthday", "birthday_source", "organization", "job_title",
                "city", "country", "how_met", "met_date", "cultural_notes",
                "preferred_channel", "communication_style", "best_contact_time",
                "language_preference", "linkedin_url", "github_url",
                "twitter_handle", "website", "notes",
            ]
            if primary_cm:
                updates = {}
                for col in merge_cols:
                    primary_val = primary_cm[col]
                    secondary_val = secondary_cm[col]
                    if primary_val is None and secondary_val is not None:
                        updates[col] = secondary_val
                if updates:
                    updates["last_manual_update"] = now
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    self.conn.execute(
                        f"UPDATE contact_metadata SET {set_clause} WHERE person_id = ?",
                        list(updates.values()) + [primary_id],
                    )
            else:
                # Primary has no metadata — re-assign
                self.conn.execute(
                    "UPDATE contact_metadata SET person_id = ? WHERE person_id = ?",
                    (primary_id, secondary_id),
                )
            # Remove secondary's metadata if still present
            self.conn.execute(
                "DELETE FROM contact_metadata WHERE person_id = ?", (secondary_id,)
            )

        # 7. Move aliases
        aliases = self.conn.execute(
            "SELECT alias FROM aliases WHERE person_id = ?", (secondary_id,)
        ).fetchall()
        for a in aliases:
            try:
                self.conn.execute(
                    "UPDATE aliases SET person_id = ? WHERE alias = ?",
                    (primary_id, a["alias"]),
                )
                summary["aliases_moved"] += 1
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "DELETE FROM aliases WHERE alias = ? AND person_id = ?",
                    (a["alias"], secondary_id),
                )

        # 8. Set secondary's merge_target_id
        self.conn.execute(
            "UPDATE people SET merge_target_id = ? WHERE id = ?",
            (primary_id, secondary_id),
        )

        # 9. Archive secondary
        self.conn.execute(
            "UPDATE people SET is_archived = 1, updated_at = ? WHERE id = ?",
            (now, secondary_id),
        )

        # 10. Add secondary's name as alias for primary
        secondary_name = self.conn.execute(
            "SELECT canonical_name FROM people WHERE id = ?", (secondary_id,)
        ).fetchone()
        if secondary_name and secondary_name["canonical_name"]:
            alias_val = secondary_name["canonical_name"].lower().strip()
            try:
                self.conn.execute(
                    "INSERT INTO aliases (alias, person_id, type, priority, created_at) VALUES (?, ?, 'merged_name', 0, ?)",
                    (alias_val, primary_id, now),
                )
            except sqlite3.IntegrityError:
                pass  # Alias already exists

        # 11. Log to hygiene_decision (if table exists)
        if _has_table(self.conn, "hygiene_decision"):
            decision_id = _gen_id("hd")
            self.conn.execute(
                """
                INSERT INTO hygiene_decision (id, queue_id, decision, decided_by, notes, decided_at)
                VALUES (?, ?, 'merge_executed', 'system', ?, ?)
                """,
                (decision_id, _gen_id("hq"), f"Merged {secondary_id} into {primary_id}", now),
            )

        self.conn.commit()
        return summary

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    def approve_issue(self, queue_id: str) -> dict[str, Any]:
        """Approve a hygiene queue item. Executes merge if action_type is 'merge'."""
        now = _now()

        row = self.conn.execute(
            "SELECT * FROM hygiene_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if not row:
            return {"error": "Queue item not found"}

        if row["status"] != "pending":
            return {"error": f"Item already {row['status']}"}

        result: dict[str, Any] = {"queue_id": queue_id, "action_type": row["action_type"]}

        if row["action_type"] == "merge" and row["person_a_id"] and row["person_b_id"]:
            merge_result = self.merge(row["person_a_id"], row["person_b_id"])
            result["merge_result"] = merge_result

        # Update queue status
        self.conn.execute(
            "UPDATE hygiene_queue SET status = 'approved', resolved_at = ? WHERE id = ?",
            (now, queue_id),
        )

        # Log decision
        decision_id = _gen_id("hd")
        self.conn.execute(
            """
            INSERT INTO hygiene_decision (id, queue_id, decision, decided_by, decided_at)
            VALUES (?, ?, 'approved', 'operator', ?)
            """,
            (decision_id, queue_id, now),
        )

        self.conn.commit()
        result["status"] = "approved"
        return result

    def reject_issue(self, queue_id: str, notes: str = "") -> dict[str, Any]:
        """Reject a hygiene queue item."""
        now = _now()

        row = self.conn.execute(
            "SELECT * FROM hygiene_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if not row:
            return {"error": "Queue item not found"}

        if row["status"] != "pending":
            return {"error": f"Item already {row['status']}"}

        self.conn.execute(
            "UPDATE hygiene_queue SET status = 'rejected', resolved_at = ? WHERE id = ?",
            (now, queue_id),
        )

        decision_id = _gen_id("hd")
        self.conn.execute(
            """
            INSERT INTO hygiene_decision (id, queue_id, decision, decided_by, notes, decided_at)
            VALUES (?, ?, 'rejected', 'operator', ?, ?)
            """,
            (decision_id, queue_id, notes, now),
        )

        self.conn.commit()
        return {"queue_id": queue_id, "status": "rejected", "notes": notes}

    # ------------------------------------------------------------------
    # Bulk Operations
    # ------------------------------------------------------------------

    def bulk_process(self, min_confidence: float = 0.9, action: str = "approve") -> dict[str, int]:
        """Bulk approve or reject hygiene queue items above a confidence threshold.

        Only processes items where action_type = 'merge' and both persons share
        at least one identifier (phone, email, wa_jid) to prevent false merges.

        Returns {processed: N, skipped: N, errors: N}.
        """
        stats = {"processed": 0, "skipped": 0, "errors": 0}

        rows = self.conn.execute(
            "SELECT id, person_a_id, person_b_id, confidence, action_type, reason "
            "FROM hygiene_queue WHERE status = 'pending' AND confidence >= ?",
            (min_confidence,),
        ).fetchall()

        for row in rows:
            qid = row["id"]

            # Safety: for merges, verify shared identifier exists
            if row["action_type"] == "merge" and action == "approve":
                shared = self.conn.execute(
                    """SELECT 1 FROM person_identifiers a
                       JOIN person_identifiers b
                         ON a.type = b.type AND lower(a.value) = lower(b.value)
                       WHERE a.person_id = ? AND b.person_id = ?
                       LIMIT 1""",
                    (row["person_a_id"], row["person_b_id"]),
                ).fetchone()
                if not shared:
                    stats["skipped"] += 1
                    continue

            try:
                if action == "approve":
                    self.approve_issue(qid)
                else:
                    self.reject_issue(qid, notes="bulk_reject")
                stats["processed"] += 1
            except Exception as e:
                logger.warning("bulk_process error on %s: %s", qid, e)
                stats["errors"] += 1

        return stats

    def queue_stats(self) -> dict[str, Any]:
        """Summary of hygiene queue by status and action_type."""
        rows = self.conn.execute("""
            SELECT status, action_type, COUNT(*) as count,
                   ROUND(AVG(confidence), 2) as avg_conf,
                   MIN(confidence) as min_conf,
                   MAX(confidence) as max_conf
            FROM hygiene_queue
            GROUP BY status, action_type
            ORDER BY status, action_type
        """).fetchall()
        return [dict(r) for r in rows]

    def scan_lifecycle_candidates(self, stale_days: int = 730) -> list[dict[str, Any]]:
        """Find people who should transition lifecycle state.

        Detects:
        - Likely stale: importance >= 3, no interaction in stale_days, no pinned importance
        - Ghost contacts: no identifiers AND no interactions AND no signals
        """
        now = _now()
        cutoff = now - (stale_days * 86400)
        candidates: list[dict[str, Any]] = []

        # Stale contacts: no interaction in N days, low importance, not pinned
        stale = self.conn.execute("""
            SELECT p.id, p.canonical_name, p.importance,
                   rs.last_interaction_at, rs.days_since_contact
            FROM people p
            LEFT JOIN relationship_state rs ON rs.person_id = p.id
            WHERE p.is_archived = 0
              AND COALESCE(p.lifecycle_state, 'active') = 'active'
              AND p.pinned_importance IS NULL
              AND p.is_self = 0
              AND p.importance >= 3
              AND (rs.last_interaction_at IS NULL OR rs.last_interaction_at < ?)
        """, (cutoff,)).fetchall()

        for row in stale:
            candidates.append({
                "person_id": row["id"],
                "name": row["canonical_name"],
                "suggested_state": "archived",
                "reason": f"No interaction in {stale_days}+ days, importance={row['importance']}",
                "days_since": row["days_since_contact"],
            })

        # Ghost contacts: exist in people but have nothing
        ghosts = self.conn.execute("""
            SELECT p.id, p.canonical_name
            FROM people p
            WHERE p.is_archived = 0
              AND COALESCE(p.lifecycle_state, 'active') = 'active'
              AND p.is_self = 0
              AND p.id NOT IN (SELECT DISTINCT person_id FROM signal_store)
              AND p.id NOT IN (SELECT DISTINCT person_id FROM interactions)
              AND p.id NOT IN (SELECT DISTINCT person_id FROM person_identifiers)
        """).fetchall()

        for row in ghosts:
            candidates.append({
                "person_id": row["id"],
                "name": row["canonical_name"],
                "suggested_state": "archived",
                "reason": "Ghost contact: no signals, no interactions, no identifiers",
            })

        return candidates
