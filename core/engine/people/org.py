"""Organization inference from contact metadata and email domains.

Infers organization records from contact_metadata.organization field
and email domains, deduplicates them, and creates membership records.
"""

from __future__ import annotations

import re
import sqlite3
import string
import time
from pathlib import Path
from random import choices
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

_ID_CHARS = string.ascii_lowercase + string.digits

# City names to filter out of organization values
_KNOWN_CITIES = {
    "dubai", "abu dhabi", "sharjah", "ajman", "rak",
    "islamabad", "lahore", "karachi",
    "riyadh", "jeddah", "makkah", "madinah",
}

# Common email providers (not real organizations)
_COMMON_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com",
    "live.com", "msn.com", "me.com", "mac.com",
    "yahoo.co.uk", "hotmail.co.uk", "googlemail.com",
    "ymail.com", "rocketmail.com",
}

# Generic / garbage organization values to skip
_GENERIC_ORGS = {
    "", "n/a", "na", "none", "self", "self-employed", "freelance",
    "freelancer", "independent", "retired", "student", "unemployed",
    "-", ".", "...", "unknown",
}

# Seniority parsing patterns
_SENIORITY_MAP: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(ceo|founder|co-founder|owner|president|principal|managing\s+director)\b", re.I), 6),
    (re.compile(r"\b(vp|vice\s+president|svp|evp|c[ft]o|coo|cio|cto|cmo)\b", re.I), 5),
    (re.compile(r"\b(director|gm|general\s+manager)\b", re.I), 4),
    (re.compile(r"\b(manager|head|lead|team\s+lead|supervisor|coordinator)\b", re.I), 3),
    (re.compile(r"\b(senior|sr\.?|principal|staff)\b", re.I), 2),
]


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


def _normalize_org_name(name: str) -> str:
    """Normalize an organization name for dedup comparison."""
    n = name.strip()
    # Remove common suffixes
    for suffix in (" LLC", " Inc", " Inc.", " Ltd", " Ltd.", " Corp", " Corp.",
                   " GmbH", " FZCO", " FZE", " FZ-LLC", " DMCC", " FZLLC"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip()


def _extract_domain(email: str) -> str | None:
    """Extract domain from an email address."""
    if "@" not in email:
        return None
    domain = email.split("@", 1)[1].lower().strip()
    if not domain or "." not in domain:
        return None
    return domain


# ---------------------------------------------------------------------------
# OrgInference
# ---------------------------------------------------------------------------


class OrgInference:
    """Infer organizations from metadata and email domains."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._conn = _connect(conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _find_or_create_org(
        self,
        name: str,
        domain: str | None = None,
        org_type: str | None = None,
    ) -> str:
        """Find an existing org by normalized name or domain, or create one.

        Returns the organization id.
        """
        norm_name = _normalize_org_name(name)

        if not _has_table(self.conn, "organization"):
            return _gen_id("org")  # No-op: table not available yet

        # Try exact match on name
        row = self.conn.execute(
            "SELECT id FROM organization WHERE name = ? LIMIT 1",
            (norm_name,),
        ).fetchone()
        if row:
            return row["id"]

        # Try case-insensitive match
        row = self.conn.execute(
            "SELECT id FROM organization WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (norm_name,),
        ).fetchone()
        if row:
            return row["id"]

        # Try domain match
        if domain:
            row = self.conn.execute(
                "SELECT id FROM organization WHERE domain = ? LIMIT 1",
                (domain,),
            ).fetchone()
            if row:
                return row["id"]

        # Create new org
        org_id = _gen_id("org")
        now = _now()
        self.conn.execute(
            """
            INSERT INTO organization (id, name, type, domain, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (org_id, norm_name, org_type, domain, now),
        )
        return org_id

    # ------------------------------------------------------------------
    # Inference from metadata
    # ------------------------------------------------------------------

    def infer_from_metadata(self) -> list[dict[str, Any]]:
        """Extract distinct organization values from contact_metadata.

        Filters out cities, generic values, and empty strings.
        Creates organization records for valid entries.

        Returns list of dicts: {org_id, name, source: 'metadata'}
        """
        rows = self.conn.execute(
            "SELECT DISTINCT organization FROM contact_metadata WHERE organization IS NOT NULL"
        ).fetchall()

        created: list[dict[str, Any]] = []

        for row in rows:
            org_name = row["organization"].strip()

            # Skip generic values
            if org_name.lower() in _GENERIC_ORGS:
                continue

            # Skip known city names
            if org_name.lower() in _KNOWN_CITIES:
                continue

            # Skip very short values (likely noise)
            if len(org_name) < 2:
                continue

            org_id = self._find_or_create_org(org_name)
            created.append({
                "org_id": org_id,
                "name": _normalize_org_name(org_name),
                "source": "metadata",
            })

        self.conn.commit()
        return created

    # ------------------------------------------------------------------
    # Inference from email domains
    # ------------------------------------------------------------------

    def infer_from_domains(self) -> list[dict[str, Any]]:
        """Extract unique email domains from contact_point.

        Skips common providers. Creates organization records with domain set.
        Returns list of dicts: {org_id, name, domain, source: 'email_domain'}
        """
        created: list[dict[str, Any]] = []

        # From contact_point table (may not exist if migration 027 not applied)
        cp_rows = []
        if _has_table(self.conn, "contact_point"):
            cp_rows = self.conn.execute(
                "SELECT DISTINCT value FROM contact_point WHERE type = 'email' AND value IS NOT NULL"
            ).fetchall()

        # Also from person_identifiers
        pi_rows = self.conn.execute(
            "SELECT DISTINCT value FROM person_identifiers WHERE type = 'email' AND value IS NOT NULL"
        ).fetchall()

        seen_domains: set[str] = set()
        all_emails = list(cp_rows) + list(pi_rows)

        for row in all_emails:
            domain = _extract_domain(row["value"])
            if not domain:
                continue
            if domain in _COMMON_EMAIL_PROVIDERS:
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            # Derive org name from domain (strip TLD, capitalize)
            name_part = domain.split(".")[0]
            org_name = name_part.replace("-", " ").replace("_", " ").title()

            if len(org_name) < 2:
                continue

            org_id = self._find_or_create_org(org_name, domain=domain, org_type="company")
            created.append({
                "org_id": org_id,
                "name": org_name,
                "domain": domain,
                "source": "email_domain",
            })

        self.conn.commit()
        return created

    # ------------------------------------------------------------------
    # Memberships
    # ------------------------------------------------------------------

    def create_memberships(self) -> int:
        """Create membership records linking people to their organizations.

        For each person with an organization in contact_metadata:
        find or create the org record, create membership with role from job_title.

        Returns count of memberships created.
        """
        if not _has_table(self.conn, "membership") or not _has_table(self.conn, "organization"):
            return 0

        rows = self.conn.execute(
            """
            SELECT cm.person_id, cm.organization, cm.job_title
            FROM contact_metadata cm
            WHERE cm.organization IS NOT NULL
            """
        ).fetchall()

        count = 0
        now = _now()

        for row in rows:
            org_name = row["organization"].strip()

            if org_name.lower() in _GENERIC_ORGS or org_name.lower() in _KNOWN_CITIES:
                continue
            if len(org_name) < 2:
                continue

            org_id = self._find_or_create_org(org_name)
            role = row["job_title"]

            # Check if membership already exists
            existing = self.conn.execute(
                """
                SELECT 1 FROM membership
                WHERE person_id = ? AND org_id = ? AND (role IS ? OR role = ?)
                LIMIT 1
                """,
                (row["person_id"], org_id, role, role),
            ).fetchone()

            if existing:
                continue

            mem_id = _gen_id("mem")
            self.conn.execute(
                """
                INSERT OR IGNORE INTO membership (id, person_id, org_id, role, source, created_at)
                VALUES (?, ?, ?, ?, 'inferred', ?)
                """,
                (mem_id, row["person_id"], org_id, role, now),
            )
            count += 1

        self.conn.commit()
        return count

    # ------------------------------------------------------------------
    # Seniority Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_seniority(job_title: str) -> int:
        """Return a numeric seniority level from a job title.

        Levels:
            6 = CEO, Founder, President
            5 = VP, SVP, C-suite (CFO, CTO, COO, CIO, CMO)
            4 = Director, General Manager
            3 = Manager, Head, Team Lead
            2 = Senior, Principal, Staff
            1 = Individual Contributor / default
        """
        if not job_title:
            return 1

        for pattern, level in _SENIORITY_MAP:
            if pattern.search(job_title):
                return level

        return 1

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Full pipeline: infer orgs from metadata + domains, create memberships.

        Returns statistics dict.
        """
        meta_orgs = self.infer_from_metadata()
        domain_orgs = self.infer_from_domains()
        memberships_created = self.create_memberships()

        # Count unique orgs in the database
        total_org_count = 0
        total_mem_count = 0
        if _has_table(self.conn, "organization"):
            total_orgs = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM organization"
            ).fetchone()
            total_org_count = total_orgs["cnt"] if total_orgs else 0
        if _has_table(self.conn, "membership"):
            total_memberships = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM membership"
            ).fetchone()
            total_mem_count = total_memberships["cnt"] if total_memberships else 0

        return {
            "orgs_from_metadata": len(meta_orgs),
            "orgs_from_domains": len(domain_orgs),
            "memberships_created": memberships_created,
            "total_organizations": total_org_count,
            "total_memberships": total_mem_count,
        }
