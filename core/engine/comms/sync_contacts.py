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
import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# People DB access. sync_contacts.py lives at core/engine/comms/ — the people
# package is a sibling under engine, i.e. parents[1]/"people". Resolved by fixed
# depth rather than by walking for a parent literally named "engine": .resolve()
# rewrites the ~/aos → aos-releases/<version> symlink but preserves the
# core/engine/comms structure, so the relative depth is stable while a name-walk
# can be defeated by the layout. (Same reasoning as patterns/compute.py.)
#
# This import broke for three months when the people package moved from
# ~/.aos/services/people to core/engine/people and three separate hand-rolled
# bootstraps — here, in patterns/compute.py, and in graduation/runner.py — were
# not updated together. Keep the three in the same shape.
_PEOPLE_SERVICE = Path(__file__).resolve().parents[1] / "people"
if str(_PEOPLE_SERVICE) not in sys.path:
    sys.path.insert(0, str(_PEOPLE_SERVICE))

try:
    import db as people_db
except ImportError:
    print("People DB not available at", _PEOPLE_SERVICE)
    sys.exit(1)

# Full Disk Access probing, same helper imessage_desktop.py and
# apple_mail_desktop.py use for other TCC-protected sources. sync_contacts.py
# lives at core/engine/comms/ — parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.util.macos_protected import has_full_disk_access  # noqa: E402

# Cron audit (2026-09-13, aos#240.4): this cron found 0 macOS contacts in
# ~90% of its runs over 4 months. Two distinct bugs collapsed into one
# silent "0 new, 0 updated, 0 unchanged" every time:
#   1. It picked `sources[0]` alphabetically among possibly several
#      Sources/<uuid>/ account folders — not necessarily the one with any
#      contacts in it.
#   2. A TCC (Full Disk Access) denial and a genuinely-empty AddressBook
#      both silently returned {} — indistinguishable from each other, and
#      from a real "nothing changed" run.
# STATUS_FILE records which of "ok" / "denied" / "not_found" actually
# happened on the last run, so core/infra/reconcile/checks/
# contact_sync_health.py can NOTIFY on a real, persistent access failure
# instead of it reading as quiet success forever. The script itself still
# always exits 0 on this condition — a TCC denial is an expected, recoverable
# state, not a crash.
STATUS_FILE = Path.home() / ".aos" / "data" / "contact-sync-status.json"


def _addressbook_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "AddressBook"


def _write_status(result: str, detail: str = "") -> None:
    """Best-effort breadcrumb of the last run's outcome. Must never raise —
    a status-recording failure is not a reason to fail the sync itself."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps({
            "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "result": result,  # "ok" | "denied" | "not_found"
            "detail": detail,
        }, indent=2))
    except OSError:
        pass


def _find_richest_source(ab_dir: Path) -> Path | None:
    """Pick the AddressBook-v*.abcddb with the most ZABCDRECORD rows.

    Multiple Sources/<uuid>/ folders exist on a real machine (one per synced
    account — iCloud, Exchange, "On My Mac", ...); the richest by row count
    is almost always the one with real contact data. Mirrors the richest-
    wins strategy core/engine/people/intel/sources/apple_contacts.py already
    uses for the same database. Version-agnostic (`v*`, not a hardcoded
    `v22`) — the previous hardcoded glob would silently find nothing the
    day Apple bumps the schema version.
    """
    candidates = sorted(ab_dir.glob("Sources/*/AddressBook-v*.abcddb"))
    if not candidates:
        # Some setups keep the DB directly under AddressBook/ rather than
        # under a Sources/<uuid>/ subfolder.
        candidates = sorted(ab_dir.glob("AddressBook-v*.abcddb"))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    best, best_count = candidates[0], -1
    for cand in candidates:
        if not has_full_disk_access(cand):
            continue  # denied candidates never win "richest" — see caller
        try:
            conn = sqlite3.connect(f"file:{cand}?mode=ro", uri=True)
            count = conn.execute("SELECT COUNT(*) FROM ZABCDRECORD").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            continue
        if count > best_count:
            best, best_count = cand, count
    return best


def _normalize_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _copy_db(path: Path) -> str | None:
    if not path.exists():
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        shutil.copy2(path, tmp.name)
    except (PermissionError, OSError):
        Path(tmp.name).unlink(missing_ok=True)
        return None
    for ext in ["-wal", "-shm"]:
        w = path.parent / (path.name + ext)
        if w.exists():
            try:
                shutil.copy2(w, tmp.name + ext)
            except (PermissionError, OSError):
                pass  # WAL/SHM not always present or readable; ignore
    return tmp.name


def read_mac_contacts_phones(ab_dir: Path | None = None) -> dict[str, dict]:
    """Read macOS Contacts and return {normalized_phone: {name, phones, emails}}.

    Returns a dict keyed by primary normalized phone for delta comparison.
    Records the outcome to STATUS_FILE ("ok" / "denied" / "not_found") —
    see the module docstring above for why that distinction exists.
    """
    if ab_dir is None:
        ab_dir = _addressbook_dir()

    source = _find_richest_source(ab_dir)
    if source is None:
        msg = f"No AddressBook-v*.abcddb found under {ab_dir}"
        log.warning(msg)
        _write_status("not_found", msg)
        return {}

    if not has_full_disk_access(source):
        msg = f"[contact-sync] AddressBook access denied at {source} — grant Contacts/Full Disk Access to this Python binary"
        print(msg)
        _write_status("denied", msg)
        return {}

    tmp_path = _copy_db(source)
    if not tmp_path:
        msg = f"[contact-sync] AddressBook access denied while copying {source}"
        print(msg)
        _write_status("denied", msg)
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

    _write_status("ok", f"{len(contacts)} contact(s) read from {source}")
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
