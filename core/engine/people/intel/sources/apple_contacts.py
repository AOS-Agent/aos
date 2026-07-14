"""Apple Contacts signal adapter.

Extracts metadata + group-membership signals from the macOS AddressBook
(`AddressBook-v22.abcddb`). There can be multiple source databases on disk
(one per configured account). We pick the one with the highest
ZABCDRECORD count — usually the iCloud source.

Contact metadata is the single densest source of declarative information
about a person: birthdays, related names ("sister", "spouse"), postal
addresses, notes, social handles, URLs, groups, job titles, organization.
The `richness_score` feeds the classifier directly.

Timestamps in AddressBook are Core Data / Apple epoch (seconds since
2001-01-01). Some older databases stored ZBIRTHDAY as unix seconds; we
fall back gracefully and reject any year outside [1900, 2100].

This adapter ONLY produces signals for persons already present in the
person_index passed to extract_all(). Matching is by:
  1. Full name (case-insensitive)
  2. First name — only if unambiguous (exactly one contact)
  3. Phone-number suffix (last 10 digits)
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import ClassVar

from ..types import (
    GroupSignal,
    MetadataSignal,
    PersonSignals,
    SignalType,
)
from .base import SignalAdapter

logger = logging.getLogger(__name__)


APPLE_EPOCH_OFFSET = 978307200  # 2001-01-01 UTC in unix seconds

_DEFAULT_GLOB = (
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)

_GROUP_KEYWORDS: dict[str, list[str]] = {
    "religious": ["masjid", "mosque", "quran", "islam", "halaqa"],
    "family": ["family", "cousin", "sibling", "parent"],
    "work": ["work", "team", "office", "project", "colleague"],
    "social": ["friend", "crew", "gang"],
}


# ── Helpers ──────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _phone_suffix(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    return digits[-10:]


def _ts_to_iso_date(ts: float | None) -> str | None:
    """Convert a Core-Data / unix timestamp to YYYY-MM-DD.

    Birthdays in AddressBook are usually Core Data seconds (since 2001-01-01)
    but very old databases used unix seconds. We detect by magnitude: very
    small values are assumed to already be in unix seconds.
    """
    if ts is None:
        return None
    try:
        f = float(ts)
    except (TypeError, ValueError):
        return None
    # Core Data seconds (since 2001-01-01) can be negative for pre-2001 dates
    # (common for birthdays). Add the offset and let the year-range check
    # below reject anything that lands outside [1900, 2100].
    unix = f + APPLE_EPOCH_OFFSET
    # If adding the offset pushes us to an absurd year, maybe it was
    # already unix seconds — try without.
    try:
        dt = datetime.fromtimestamp(unix, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        try:
            dt = datetime.fromtimestamp(f, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if dt.year < 1900 or dt.year > 2100:
        # Try interpreting raw as unix instead
        try:
            dt = datetime.fromtimestamp(f, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        if dt.year < 1900 or dt.year > 2100:
            return None
    return dt.strftime("%Y-%m-%d")


def _ts_to_yearless_date(ts: float | None) -> str | None:
    """Convert a Core-Data / unix timestamp to ``--MM-DD`` format.

    ``ZBIRTHDAYYEARLESS`` stores month-day only values (the year component
    is meaningless — Apple uses a placeholder anchor year). We therefore
    extract month and day and emit the ``--MM-DD`` form (ISO 8601's
    "no year" birthday shape). Any timestamp that fails to parse returns
    ``None``.
    """
    if ts is None:
        return None
    try:
        f = float(ts)
    except (TypeError, ValueError):
        return None
    # Try Core-Data epoch first, then raw unix as fallback.
    dt = None
    for candidate in (f + APPLE_EPOCH_OFFSET, f):
        try:
            dt = datetime.fromtimestamp(candidate, tz=timezone.utc)
            break
        except (OverflowError, OSError, ValueError):
            dt = None
            continue
    if dt is None:
        return None
    return f"--{dt.month:02d}-{dt.day:02d}"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if ``column`` exists on ``table``."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return any(r[1] == column for r in rows)


def _ts_to_iso_full(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        f = float(ts)
    except (TypeError, ValueError):
        return None
    unix = f + APPLE_EPOCH_OFFSET
    try:
        dt = datetime.fromtimestamp(unix, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if dt.year < 1900 or dt.year > 2100:
        return None
    return dt.isoformat()


def _categorize_group(name: str) -> list[str]:
    lowered = (name or "").lower()
    hits: list[str] = []
    for category, keywords in _GROUP_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            hits.append(category)
    return hits


def _count_records(path: str) -> int:
    """Open read-only and count ZABCDRECORD rows. Returns -1 on error."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return -1
    try:
        row = conn.execute("SELECT COUNT(*) FROM ZABCDRECORD").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1
    finally:
        conn.close()


# ── Adapter ─────────────────────────────────────────────────────────

class AppleContactsAdapter(SignalAdapter):
    """Extracts metadata and group-membership signals from macOS AddressBook."""

    name: ClassVar[str] = "apple_contacts"
    display_name: ClassVar[str] = "Apple Contacts"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [
        SignalType.METADATA,
        SignalType.GROUP_MEMBERSHIP,
    ]
    description: ClassVar[str] = (
        "Contact metadata, related names, groups via AddressBook"
    )
    requires: ClassVar[list[str]] = [
        "file:~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb",
    ]

    # Class attribute so tests can monkeypatch the glob pattern.
    _glob_pattern: ClassVar[str] = _DEFAULT_GLOB

    def __init__(self, db_path: str | None = None):
        self._explicit_path = db_path
        self._db_path: str | None = None

    # ── Path selection ──

    def _resolve_db_path(self) -> str | None:
        if self._db_path:
            return self._db_path
        if self._explicit_path:
            self._db_path = self._explicit_path
            return self._db_path
        pattern = os.path.expanduser(self._glob_pattern)
        candidates = glob.glob(pattern)
        if not candidates:
            return None
        scored: list[tuple[int, str]] = []
        for c in candidates:
            n = _count_records(c)
            if n >= 0:
                scored.append((n, c))
        if not scored:
            return None
        scored.sort(key=lambda t: t[0], reverse=True)
        self._db_path = scored[0][1]
        return self._db_path

    # ── Base interface ──

    def is_available(self) -> bool:
        try:
            path = self._resolve_db_path()
            if not path or not os.path.exists(path):
                return False
            # Must also have a readable ZABCDRECORD table
            return _count_records(path) >= 0
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("apple_contacts is_available failed: %s", e)
            return False

    def extract_all(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        path = self._resolve_db_path()
        if not path or not os.path.exists(path):
            return {}

        tmpdir = tempfile.mkdtemp(prefix="aos-apple-contacts-")
        temp_db = os.path.join(tmpdir, "AddressBook-v22.abcddb")
        try:
            try:
                shutil.copy2(path, temp_db)
            except OSError as e:
                logger.warning("apple_contacts: failed to copy db: %s", e)
                return {}

            try:
                conn = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
            except sqlite3.Error as e:
                logger.warning("apple_contacts: failed to open db: %s", e)
                return {}

            try:
                return self._extract(conn, person_index)
            except Exception as e:  # pragma: no cover - defensive
                logger.exception("apple_contacts: extraction failed: %s", e)
                return {}
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Core extraction ──

    def _extract(
        self,
        conn: sqlite3.Connection,
        person_index: dict[str, dict],
    ) -> dict[str, PersonSignals]:
        if not _table_exists(conn, "ZABCDRECORD"):
            return {}

        # 1) Load base records into memory
        records: dict[int, dict] = {}
        first_name_to_ids: dict[str, list[int]] = defaultdict(list)
        full_name_to_id: dict[str, int] = {}

        # ZNOTE in ZABCDRECORD is an INTEGER flag in modern macOS; real note
        # text lives in ZABCDNOTE(ZCONTACT, ZTEXT). We fetch it below.
        for row in conn.execute(
            "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, "
            "ZJOBTITLE, ZBIRTHDAY, ZCREATIONDATE FROM ZABCDRECORD"
        ):
            z_pk, first, last, org, job, birthday, created = row
            records[z_pk] = {
                "z_pk": z_pk,
                "first": first or "",
                "last": last or "",
                "org": org,
                "job": job,
                "note": None,  # filled in from ZABCDNOTE below
                "birthday": birthday,
                "created": created,
            }
            fn = (first or "").strip().lower()
            ln = (last or "").strip().lower()
            full = f"{fn} {ln}".strip()
            if full:
                full_name_to_id.setdefault(full, z_pk)
            if fn:
                first_name_to_ids[fn].append(z_pk)

        # 1b) Second pass — ZBIRTHDAYYEARLESS column holds month-day-only
        # birthdays for contacts whose year is unknown. This is where most
        # birthday data actually lives on modern macOS (ZBIRTHDAY often
        # has only a handful of rows and many are pre-1900 placeholders).
        # We always capture the yearless value when present; the final
        # birthday computation below prefers year-aware ZBIRTHDAY and
        # falls back to yearless only when ZBIRTHDAY is missing OR fails
        # the [1900, 2100] year-range check.
        if _column_exists(conn, "ZABCDRECORD", "ZBIRTHDAYYEARLESS"):
            try:
                for row in conn.execute(
                    "SELECT Z_PK, ZBIRTHDAYYEARLESS FROM ZABCDRECORD "
                    "WHERE ZBIRTHDAYYEARLESS IS NOT NULL"
                ):
                    pk, yearless_ts = row
                    rec = records.get(pk)
                    if not rec:
                        continue
                    rec["birthday_yearless"] = yearless_ts
            except sqlite3.Error as e:
                logger.debug(
                    "apple_contacts: ZBIRTHDAYYEARLESS read failed: %s", e
                )

        # 2) Phone suffix index
        phone_suffix_to_id: dict[str, int] = {}
        if _table_exists(conn, "ZABCDPHONENUMBER"):
            for row in conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"
            ):
                owner, fullnumber = row
                suffix = _phone_suffix(fullnumber)
                if suffix and suffix not in phone_suffix_to_id:
                    phone_suffix_to_id[suffix] = owner

        # 3) Match persons to contact Z_PKs
        person_to_pk: dict[str, int] = {}
        for person_id, ident in (person_index or {}).items():
            name = (ident.get("name") or "").strip().lower()
            matched: int | None = None
            if name and name in full_name_to_id:
                matched = full_name_to_id[name]
            if matched is None and name:
                # Try first-name-only (before any space)
                first_only = name.split(" ", 1)[0]
                if first_only and first_only in first_name_to_ids:
                    ids = first_name_to_ids[first_only]
                    if len(ids) == 1:
                        matched = ids[0]
            if matched is None:
                for phone in ident.get("phones") or []:
                    suffix = _phone_suffix(phone)
                    if suffix and suffix in phone_suffix_to_id:
                        matched = phone_suffix_to_id[suffix]
                        break
            if matched is not None:
                person_to_pk[person_id] = matched

        if not person_to_pk:
            return {}

        matched_pks = set(person_to_pk.values())

        # 4) Collect supporting data per matched Z_PK
        addresses: dict[int, list[dict]] = defaultdict(list)
        if _table_exists(conn, "ZABCDPOSTALADDRESS"):
            for row in conn.execute(
                "SELECT ZOWNER, ZCITY, ZCOUNTRYNAME FROM ZABCDPOSTALADDRESS"
            ):
                owner, city, country = row
                if owner in matched_pks:
                    addresses[owner].append(
                        {"city": city or "", "country": country or ""}
                    )

        social: dict[int, list[dict]] = defaultdict(list)
        if _table_exists(conn, "ZABCDSOCIALPROFILE"):
            try:
                for row in conn.execute(
                    "SELECT ZOWNER, ZSERVICENAME, ZUSERNAME FROM ZABCDSOCIALPROFILE"
                ):
                    owner, service, username = row
                    if owner in matched_pks and (service or username):
                        social[owner].append(
                            {
                                "platform": service or "",
                                "handle": username or "",
                            }
                        )
            except sqlite3.Error as e:
                logger.debug("apple_contacts: social profile read failed: %s", e)

        related: dict[int, list[dict]] = defaultdict(list)
        if _table_exists(conn, "ZABCDRELATEDNAME"):
            for row in conn.execute(
                "SELECT ZOWNER, ZLABEL, ZNAME FROM ZABCDRELATEDNAME"
            ):
                owner, label, rname = row
                if owner in matched_pks and (label or rname):
                    related[owner].append(
                        {"label": label or "", "name": rname or ""}
                    )

        urls: dict[int, list[str]] = defaultdict(list)
        if _table_exists(conn, "ZABCDURLADDRESS"):
            try:
                for row in conn.execute(
                    "SELECT ZOWNER, ZURL FROM ZABCDURLADDRESS"
                ):
                    owner, url = row
                    if owner in matched_pks and url:
                        urls[owner].append(url)
            except sqlite3.Error as e:
                logger.debug("apple_contacts: url read failed: %s", e)

        # Notes live in a separate table on modern macOS. ZABCDNOTE.ZCONTACT
        # → ZABCDRECORD.Z_PK, ZABCDNOTE.ZTEXT is the note body. Fall back
        # gracefully if the table is absent or the schema varies.
        if _table_exists(conn, "ZABCDNOTE"):
            try:
                for row in conn.execute("SELECT ZCONTACT, ZTEXT FROM ZABCDNOTE"):
                    contact_pk, text = row
                    if contact_pk in matched_pks and text and contact_pk in records:
                        records[contact_pk]["note"] = text
            except sqlite3.Error as e:
                logger.debug("apple_contacts: note read failed: %s", e)

        # 5) Groups — build group id → name, then membership per contact.
        # Some macOS versions store groups in ZABCDGROUP, others flag rows in
        # ZABCDRECORD itself. We try ZABCDGROUP first, which is what the
        # fixture uses and what most recent macOS versions provide.
        group_names: dict[int, str] = {}
        if _table_exists(conn, "ZABCDGROUP"):
            try:
                for row in conn.execute("SELECT Z_PK, ZNAME FROM ZABCDGROUP"):
                    gid, gname = row
                    if gname:
                        group_names[gid] = gname
            except sqlite3.Error as e:
                logger.debug("apple_contacts: group list read failed: %s", e)

        memberships: dict[int, list[str]] = defaultdict(list)
        if group_names and _table_exists(conn, "Z_22PARENTGROUPS"):
            try:
                for row in conn.execute(
                    "SELECT Z_22PARENTGROUPS1, Z_22GROUPS FROM Z_22PARENTGROUPS"
                ):
                    contact_pk, group_pk = row
                    if contact_pk in matched_pks and group_pk in group_names:
                        memberships[contact_pk].append(group_names[group_pk])
            except sqlite3.Error as e:
                logger.debug(
                    "apple_contacts: group membership read failed: %s", e
                )

        # 6) Build PersonSignals per matched person
        results: dict[str, PersonSignals] = {}
        now_iso = datetime.now(timezone.utc).isoformat()

        for person_id, z_pk in person_to_pk.items():
            rec = records.get(z_pk)
            if not rec:
                continue

            # Birthday — year-aware form wins. Fall back to the yearless
            # form (--MM-DD) if the year-aware column was empty OR the
            # value failed the [1900, 2100] year-range check (common for
            # old Core Data placeholder timestamps).
            birthday_iso = _ts_to_iso_date(rec.get("birthday"))
            if birthday_iso is None:
                birthday_iso = _ts_to_yearless_date(rec.get("birthday_yearless"))
            has_birthday = birthday_iso is not None

            # Address
            addr_list = addresses.get(z_pk, [])
            has_address = bool(addr_list)

            # Notes
            note = rec.get("note")
            has_notes = bool(note)
            notes_snippet = (note[:200] if note else None)

            # Social
            social_list = social.get(z_pk, [])
            has_social_profiles = bool(social_list)

            # Related names
            related_list = related.get(z_pk, [])
            has_related_names = bool(related_list)

            # URLs
            urls_list = urls.get(z_pk, [])
            has_urls = bool(urls_list)

            # Groups
            contact_groups = memberships.get(z_pk, [])

            # Organization / job title / creation
            org = rec.get("org") or None
            job = rec.get("job") or None
            created_iso = _ts_to_iso_full(rec.get("created"))

            # Richness score — count of populated boolean fields plus
            # org/job/groups bonuses.
            richness = 0
            for flag in (
                has_birthday,
                has_address,
                has_notes,
                has_social_profiles,
                has_related_names,
                has_urls,
            ):
                if flag:
                    richness += 1
            if org:
                richness += 1
            if job:
                richness += 1
            if contact_groups:
                richness += 1

            metadata = MetadataSignal(
                source="apple_contacts",
                has_birthday=has_birthday,
                birthday=birthday_iso,
                has_address=has_address,
                addresses=addr_list,
                has_notes=has_notes,
                notes_snippet=notes_snippet,
                has_social_profiles=has_social_profiles,
                social_profiles=social_list,
                has_related_names=has_related_names,
                related_names=related_list,
                has_urls=has_urls,
                urls=urls_list,
                contact_groups=contact_groups,
                organization_raw=org,
                job_title=job,
                contact_created_at=created_iso,
                richness_score=richness,
            )

            person_name = ""
            if person_index and person_id in person_index:
                person_name = person_index[person_id].get("name") or ""
            signals = PersonSignals(
                person_id=person_id,
                person_name=person_name,
                extracted_at=now_iso,
                source_coverage=["apple_contacts"],
            )
            signals.metadata.append(metadata)

            # GroupSignal
            if contact_groups:
                category_counts: dict[str, int] = defaultdict(int)
                groups_payload: list[dict] = []
                for gname in contact_groups:
                    groups_payload.append(
                        {
                            "name": gname,
                            "type": "apple_contacts",
                            "member_count": 0,
                            "role": "member",
                        }
                    )
                    for cat in _categorize_group(gname):
                        category_counts[cat] += 1
                group_signal = GroupSignal(
                    source="apple_contacts",
                    groups=groups_payload,
                    total_groups=len(groups_payload),
                    shared_with_operator=len(groups_payload),
                    group_categories=dict(category_counts),
                )
                signals.group_membership.append(group_signal)

            results[person_id] = signals

        return results
