#!/usr/bin/env python3
"""Contact sync — incremental update from macOS Contacts to People DB.

Compares macOS Address Book against People DB and inserts new contacts
or updates changed ones. Designed to run daily (via cron) or on-demand.

Unlike bootstrap.py (full seed), this is a delta sync — fast and safe
to run repeatedly.

Usage:
  python3 sync_contacts.py              # run sync
  python3 sync_contacts.py --dry-run    # show what would change
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# People DB access
_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
sys.path.insert(0, str(_PEOPLE_SERVICE))

try:
    import db as people_db
except ImportError:
    print("People DB not available at", _PEOPLE_SERVICE)
    sys.exit(1)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _copy_db(path: Path) -> str | None:
    if not path.exists():
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    shutil.copy2(path, tmp.name)
    for ext in ["-wal", "-shm"]:
        w = path.parent / (path.name + ext)
        if w.exists():
            shutil.copy2(w, tmp.name + ext)
    return tmp.name


def read_mac_contacts_phones() -> dict[str, dict]:
    """Read macOS Contacts and return {normalized_phone: {name, phones, emails}}.

    Returns a dict keyed by primary normalized phone for delta comparison.
    """
    ab_dir = Path.home() / "Library" / "Application Support" / "AddressBook"
    sources = sorted(ab_dir.glob("Sources/*/AddressBook-v22.abcddb"))
    if not sources:
        log.warning("No AddressBook database found")
        return {}

    tmp_path = _copy_db(sources[0])
    if not tmp_path:
        return {}

    contacts = {}
    try:
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, r.ZNICKNAME
            FROM ZABCDRECORD r
            WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL
        """).fetchall()

        for row in rows:
            pk = row["Z_PK"]
            first = (row["ZFIRSTNAME"] or "").strip()
            last = (row["ZLASTNAME"] or "").strip()
            name = f"{first} {last}".strip()
            if not name:
                continue

            # Get phones
            phones = []
            for ph in conn.execute(
                "SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER = ?", (pk,)
            ).fetchall():
                if ph["ZFULLNUMBER"]:
                    phones.append(_normalize_phone(ph["ZFULLNUMBER"]))

            # Get emails
            emails = []
            for em in conn.execute(
                "SELECT ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZOWNER = ?", (pk,)
            ).fetchall():
                if em["ZADDRESS"]:
                    emails.append(em["ZADDRESS"].lower())

            if phones:
                contacts[phones[0]] = {
                    "name": name,
                    "first": first,
                    "last": last,
                    "org": (row["ZORGANIZATION"] or "").strip(),
                    "nickname": (row["ZNICKNAME"] or "").strip(),
                    "phones": phones,
                    "emails": emails,
                }

        conn.close()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return contacts


def sync(dry_run: bool = False) -> dict:
    """Run incremental sync. Returns {new: N, updated: N, unchanged: N}."""
    mac_contacts = read_mac_contacts_phones()
    if not mac_contacts:
        log.info("No macOS contacts to sync")
        return {"new": 0, "updated": 0, "unchanged": 0}

    conn = people_db.connect()
    stats = {"new": 0, "updated": 0, "unchanged": 0}

    for primary_phone, contact in mac_contacts.items():
        # Check if person exists by any phone
        existing = None
        for phone in contact["phones"]:
            normalized = f"+{phone}" if not phone.startswith("+") else phone
            existing = people_db.find_person_by_identifier(conn, "phone", normalized)
            if existing:
                break

        if not existing:
            # Check by email
            for email in contact["emails"]:
                existing = people_db.find_person_by_identifier(conn, "email", email)
                if existing:
                    break

        if existing:
            # Person exists — check if name changed
            if existing.get("canonical_name") != contact["name"]:
                if not dry_run:
                    conn.execute(
                        "UPDATE people SET canonical_name = ?, updated_at = ? WHERE id = ?",
                        (contact["name"], people_db.now_ts(), existing["id"]),
                    )
                    conn.commit()
                stats["updated"] += 1
                log.info("Updated: %s → %s", existing["canonical_name"], contact["name"])
            else:
                stats["unchanged"] += 1
        else:
            # New contact
            if not dry_run:
                person_id = people_db.insert_person(
                    conn,
                    name=contact["name"],
                    first=contact["first"],
                    last=contact["last"],
                    nickname=contact.get("nickname", ""),
                    display_name=contact["name"],
                    importance=4,  # default low, operator can promote
                )
                # Add identifiers
                for phone in contact["phones"]:
                    normalized = f"+{phone}" if not phone.startswith("+") else phone
                    people_db.add_identifier(
                        conn, person_id,
                        type="phone", value=phone, normalized=normalized,
                        is_primary=int(phone == primary_phone),
                        source="mac_contacts", label="",
                    )
                for email in contact["emails"]:
                    people_db.add_identifier(
                        conn, person_id,
                        type="email", value=email, normalized=email.lower(),
                        is_primary=0, source="mac_contacts", label="",
                    )
                # Metadata
                if contact.get("org"):
                    people_db.set_metadata(conn, person_id, organization=contact["org"])
                conn.commit()

            stats["new"] += 1
            log.info("New: %s (%d phones, %d emails)",
                     contact["name"], len(contact["phones"]), len(contact["emails"]))

    log.info("Sync complete: %d new, %d updated, %d unchanged",
             stats["new"], stats["updated"], stats["unchanged"])
    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync macOS Contacts → People DB")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = sync(dry_run=args.dry_run)
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Sync: {stats['new']} new, {stats['updated']} updated, {stats['unchanged']} unchanged")


if __name__ == "__main__":
    main()
