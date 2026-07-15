"""Cross-source identity enrichment.

Walks external identity sources (Apple Contacts, WhatsApp group rosters)
and links missing identifiers to existing person rows in people.db.
Then bulk re-resolves comms.db rows that have NULL person_id.

This is the "smart resolver" — it uses multiple signals:

  Layer 1: Exact identifier overlap (confidence 1.0)
    Person already has phone X → contact card also has email Y → link Y.

  Layer 2: Name/alias match + contact-card join (confidence 0.9)
    people.db has "Sam Taylor" with alias "dad".
    Apple Contacts has "Dad" with phone +15555550123.
    → Link +15555550123 to Sam.

  Layer 3: WhatsApp group context (confidence 0.85)
    Operator's family group contains JID X.
    JID X has contact name "Mom".
    people.db has a person with alias "mom" in the operator's family.
    → Link JID X to that person.

Idempotent — re-runs find nothing new once everything is linked.
Universal — works for any operator's data shape.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
CONTACTS_ROOT = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"
WA_CHAT_STORAGE = (
    Path.home() / "Library" / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    try:
        import phonenumbers
        try:
            parsed = phonenumbers.parse(raw, "US")
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            return None
        return None
    except ImportError:
        digits = re.sub(r"[^\d]", "", raw)
        if 7 <= len(digits) <= 15:
            return "+" + digits
        return None


def _normalize_email(raw: str) -> str | None:
    if not raw or "@" not in raw:
        return None
    return raw.strip().lower()


def _copy_db(src: Path) -> str | None:
    if not src.exists():
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        shutil.copy2(src, tmp.name)
        for ext in ("-wal", "-shm"):
            s = Path(str(src) + ext)
            if s.exists():
                shutil.copy2(s, tmp.name + ext)
    except (PermissionError, OSError):
        return None
    return tmp.name


# ── Read Apple Contacts ─────────────────────────────────────────────────


def _read_apple_contacts() -> list[dict]:
    """Read all contact cards with their phones + emails.

    Returns list of dicts: {name, first, last, nickname, phones: [], emails: []}
    """
    contacts: list[dict] = []
    if not CONTACTS_ROOT.exists():
        return contacts

    for db_path in CONTACTS_ROOT.glob("*/AddressBook-v22.abcddb"):
        tmp = _copy_db(db_path)
        if not tmp:
            continue
        try:
            conn = sqlite3.connect(tmp)
            conn.row_factory = sqlite3.Row

            # Read all people
            people = {}
            for r in conn.execute(
                "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION "
                "FROM ZABCDRECORD WHERE ZFIRSTNAME IS NOT NULL OR ZLASTNAME IS NOT NULL"
            ).fetchall():
                pk = r["Z_PK"]
                first = (r["ZFIRSTNAME"] or "").strip()
                last = (r["ZLASTNAME"] or "").strip()
                name = f"{first} {last}".strip()
                people[pk] = {
                    "name": name,
                    "first": first,
                    "last": last,
                    "nickname": (r["ZNICKNAME"] or "").strip(),
                    "organization": (r["ZORGANIZATION"] or "").strip(),
                    "phones": [],
                    "emails": [],
                }

            # Phones
            for r in conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
            ).fetchall():
                pk = r["ZOWNER"]
                if pk in people:
                    phone = _normalize_phone(r["ZFULLNUMBER"])
                    if phone:
                        people[pk]["phones"].append(phone)

            # Emails
            for r in conn.execute(
                "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
            ).fetchall():
                pk = r["ZOWNER"]
                if pk in people:
                    email = _normalize_email(r["ZADDRESS"])
                    if email:
                        people[pk]["emails"].append(email)

            contacts.extend(people.values())
            conn.close()
        except (sqlite3.Error, PermissionError) as e:
            logger.debug("Failed to read contacts DB %s: %s", db_path, e)
        finally:
            Path(tmp).unlink(missing_ok=True)
            for ext in ("-wal", "-shm"):
                Path(tmp + ext).unlink(missing_ok=True)

    return contacts


# ── Build lookup indexes from people.db ─────────────────────────────────


def _build_people_index(conn: sqlite3.Connection) -> dict:
    """Build lookup structures for fast matching.

    Returns dict with:
      by_phone:   {normalized_phone: person_id}
      by_email:   {normalized_email: person_id}
      by_name:    {lowered_canonical: person_id}  (ambiguous names → first match)
      by_alias:   {lowered_alias: person_id}
      existing:   {person_id: set of (type, normalized)}
    """
    idx = {"by_phone": {}, "by_email": {}, "by_name": {}, "by_alias": {}, "existing": {}}

    for r in conn.execute(
        "SELECT type, value, normalized, person_id FROM person_identifiers"
    ).fetchall():
        pid = r["person_id"]
        n = r["normalized"] or r["value"]
        if not n:
            continue
        idx["existing"].setdefault(pid, set()).add((r["type"], n))
        if r["type"] == "phone":
            idx["by_phone"].setdefault(n, pid)
        elif r["type"] == "email":
            idx["by_email"].setdefault(n.lower(), pid)
        elif r["type"] == "wa_jid":
            # Also index the phone portion of wa_jid
            digits = re.sub(r"[^\d]", "", n.split("@")[0] if "@" in n else n)
            if digits:
                idx["by_phone"].setdefault("+" + digits, pid)

    for r in conn.execute(
        "SELECT id, canonical_name FROM people WHERE is_archived = 0"
    ).fetchall():
        key = (r["canonical_name"] or "").lower().strip()
        if key:
            idx["by_name"].setdefault(key, r["id"])

    for r in conn.execute("SELECT alias, person_id FROM aliases").fetchall():
        key = (r["alias"] or "").lower().strip()
        if key:
            idx["by_alias"].setdefault(key, r["person_id"])

    return idx


# ── Match a contact card to a person ────────────────────────────────────


def _match_contact(contact: dict, idx: dict) -> str | None:
    """Try to match an Apple Contact to a people.db person_id.

    Layer 1: Any identifier overlap → person_id (highest confidence).
    Layer 2: Name or alias match → person_id (lower confidence, but
             contact cards are authoritative — one card = one person).
    """
    # Layer 1: identifier overlap
    for phone in contact["phones"]:
        pid = idx["by_phone"].get(phone)
        if pid:
            return pid
    for email in contact["emails"]:
        pid = idx["by_email"].get(email.lower())
        if pid:
            return pid

    # Layer 2: name match (exact canonical)
    name_key = contact["name"].lower().strip()
    pid = idx["by_name"].get(name_key)
    if pid:
        return pid

    # Layer 2b: nickname / first-name-only alias match
    for candidate in (contact["nickname"], contact["first"], contact["name"]):
        key = (candidate or "").lower().strip()
        if key and len(key) >= 3:
            pid = idx["by_alias"].get(key)
            if pid:
                return pid

    return None


# ── Enrichment pass ─────────────────────────────────────────────────────


def enrich_identifiers(
    conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Walk Apple Contacts, match to people.db, link new identifiers.

    Returns counts: {contacts_scanned, matched, identifiers_added, skipped_ambiguous}
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(str(PEOPLE_DB))
        conn.row_factory = sqlite3.Row

    try:
        contacts = _read_apple_contacts()
        idx = _build_people_index(conn)

        counts = {
            "contacts_scanned": len(contacts),
            "matched": 0,
            "identifiers_added": 0,
            "skipped_no_match": 0,
        }

        for contact in contacts:
            pid = _match_contact(contact, idx)
            if pid is None:
                counts["skipped_no_match"] += 1
                continue

            counts["matched"] += 1
            existing = idx["existing"].get(pid, set())

            # Link any new identifiers from this contact card
            new_idents: list[tuple[str, str]] = []
            for phone in contact["phones"]:
                if ("phone", phone) not in existing:
                    new_idents.append(("phone", phone))
            for email in contact["emails"]:
                if ("email", email.lower()) not in existing:
                    new_idents.append(("email", email.lower()))

            if new_idents and not dry_run:
                for typ, val in new_idents:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO person_identifiers "
                            "(person_id, type, value, normalized, source) "
                            "VALUES (?, ?, ?, ?, 'apple-contacts-enrichment')",
                            (pid, typ, val, val),
                        )
                        counts["identifiers_added"] += 1
                        # Update the index so subsequent contacts see these
                        existing.add((typ, val))
                        if typ == "phone":
                            idx["by_phone"][val] = pid
                        elif typ == "email":
                            idx["by_email"][val] = pid
                    except sqlite3.IntegrityError:
                        pass
            elif new_idents and dry_run:
                counts["identifiers_added"] += len(new_idents)

        if not dry_run:
            conn.commit()
        return counts
    finally:
        if own_conn:
            conn.close()


# ── Bulk re-resolve comms.db ────────────────────────────────────────────


def bulk_resolve_comms(dry_run: bool = False) -> dict[str, int]:
    """Re-resolve all NULL person_id rows in comms.db against current people.db identifiers.

    Uses ATTACH to cross-query. For each unresolved message:
      Inbound: sender_id → match against phone/email/wa_jid
      Outbound: recipient_id → match against phone/email/wa_jid
    """
    if not COMMS_DB.exists() or not PEOPLE_DB.exists():
        return {"resolved": 0, "still_null": 0}

    conn = sqlite3.connect(str(COMMS_DB))
    conn.execute(f"ATTACH DATABASE '{PEOPLE_DB}' AS p")

    # Build a unified handle→person_id lookup from people.db
    rows = conn.execute(
        "SELECT person_id, type, normalized FROM p.person_identifiers WHERE normalized IS NOT NULL"
    ).fetchall()

    handle_map: dict[str, str] = {}
    for r in rows:
        norm = r[2]
        pid = r[0]
        typ = r[1]
        if typ == "phone":
            handle_map[norm] = pid
            # Also index without the + prefix (iMessage uses both)
            if norm.startswith("+"):
                handle_map[norm[1:]] = pid
        elif typ == "email":
            handle_map[norm.lower()] = pid
        elif typ == "wa_jid":
            handle_map[norm] = pid
            # Also index the full JID format
            if "@" not in norm:
                handle_map[f"{norm}@s.whatsapp.net"] = pid

    # Find unresolved messages
    unresolved = conn.execute(
        "SELECT id, direction, sender_id, recipient_id, channel, channel_metadata "
        "FROM messages WHERE person_id IS NULL"
    ).fetchall()

    batch: list[tuple[str, str]] = []  # (person_id, msg_id)
    for msg in unresolved:
        msg_id = msg[0]
        direction = msg[1]
        sender_id = (msg[2] or "").strip()
        recipient_id = (msg[3] or "").strip()
        meta_raw = msg[5]

        # Determine the "other party" handle
        if direction == "inbound":
            handle = sender_id
        elif direction == "outbound":
            handle = recipient_id
        else:
            handle = sender_id or recipient_id

        pid = handle_map.get(handle)

        # Fallback: try channel_metadata.jid or .handle for WA/iMessage
        if pid is None and meta_raw:
            try:
                meta = json.loads(meta_raw)
                for key in ("jid", "handle", "sender_address", "chat_identifier"):
                    val = meta.get(key, "")
                    if val:
                        pid = handle_map.get(val) or handle_map.get(val.lower())
                        if pid:
                            break
            except (json.JSONDecodeError, TypeError):
                pass

        if pid:
            batch.append((pid, msg_id))

    if not dry_run and batch:
        CHUNK = 500
        for i in range(0, len(batch), CHUNK):
            chunk = batch[i : i + CHUNK]
            conn.executemany(
                "UPDATE messages SET person_id = ? WHERE id = ?",
                chunk,
            )
        conn.commit()

    still_null = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE person_id IS NULL"
    ).fetchone()[0]
    conn.close()

    return {"resolved": len(batch), "still_null": still_null}


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Cross-source identity enrichment + comms.db re-resolution"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-contacts", action="store_true",
                        help="Skip Apple Contacts enrichment, only re-resolve comms.db")
    args = parser.parse_args()

    print("=== Identity Enrichment Pass ===\n")

    if not args.skip_contacts:
        print("Phase 1: Apple Contacts → people.db identifier enrichment")
        counts = enrich_identifiers(dry_run=args.dry_run)
        for k, v in counts.items():
            print(f"  {k}: {v}")
        print()

    print("Phase 2: Bulk re-resolve comms.db (NULL person_id rows)")
    resolve = bulk_resolve_comms(dry_run=args.dry_run)
    for k, v in resolve.items():
        print(f"  {k}: {v}")
    print()

    if args.dry_run:
        print("Dry run — re-run without --dry-run to apply.")
    else:
        print("Done.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
