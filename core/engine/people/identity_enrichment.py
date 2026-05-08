"""Cross-source identity enrichment.

Walks external identity sources (Apple Contacts, WhatsApp group rosters)
and links missing identifiers to existing person rows in people.db.
Then bulk re-resolves comms.db rows that have NULL person_id.

This is the "smart resolver" — it uses multiple signals:

  Layer 1: Exact identifier overlap (confidence 1.0)
    Person already has phone X → contact card also has email Y → link Y.

  Layer 2: Name/alias match + contact-card join (confidence 0.9)
    people.db has "Idrees Zubair" with alias "baba".
    Apple Contacts has "Baba" with phone +14168435481.
    → Link +14168435481 to Idrees.

  Layer 3: WhatsApp group context (confidence 0.85)
    Operator's family group contains JID X.
    JID X has contact name "Mama".
    people.db has a person with alias "mama" in the operator's family.
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
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
IMESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"
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

        now = int(time.time())
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


# ── iMessage handle enrichment ──────────────────────────────────────────


def _phone_suffix(phone: str) -> str | None:
    """Return the last 10 digits of a phone number, or None if <10 digits."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else None


def enrich_imessage_handles(
    conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Read iMessage handles from chat.db and write imessage_handle identifiers.

    For each handle in chat.db (phone or email), matches to a person_id
    using the same phone-suffix and email-exact logic as the signal adapter.
    Writes the matched handle as type 'imessage_handle' in person_identifiers.

    When a person has multiple iMessage handles, the one with the most
    messages is marked is_primary=1.

    Returns counts: {handles_scanned, matched, identifiers_added, already_existed, skipped_ambiguous}
    """
    if not IMESSAGE_DB.exists():
        logger.info("enrich_imessage_handles: chat.db not found, skipping")
        return {"handles_scanned": 0, "matched": 0, "identifiers_added": 0,
                "already_existed": 0, "skipped_ambiguous": 0}

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(str(PEOPLE_DB))
        conn.row_factory = sqlite3.Row

    try:
        idx = _build_people_index(conn)

        # Build phone-suffix → pid and email → pid maps from existing identifiers.
        suffix_to_pid: dict[str, str] = {}
        ambiguous_suffixes: set[str] = set()
        email_to_pid: dict[str, str] = {}

        for pid, idents in idx["existing"].items():
            for typ, val in idents:
                if typ == "phone":
                    sfx = _phone_suffix(val)
                    if sfx:
                        existing = suffix_to_pid.get(sfx)
                        if existing and existing != pid:
                            ambiguous_suffixes.add(sfx)
                        else:
                            suffix_to_pid[sfx] = pid
                elif typ == "wa_jid":
                    # Also index phone portion of wa_jid
                    digits = re.sub(r"[^\d]", "", val.split("@")[0] if "@" in val else val)
                    sfx = digits[-10:] if len(digits) >= 10 else None
                    if sfx:
                        existing = suffix_to_pid.get(sfx)
                        if existing and existing != pid:
                            ambiguous_suffixes.add(sfx)
                        else:
                            suffix_to_pid[sfx] = pid
                elif typ == "email":
                    email_to_pid.setdefault(val.lower(), pid)

        for s in ambiguous_suffixes:
            suffix_to_pid.pop(s, None)

        # Copy chat.db safely.
        tmp = _copy_db(IMESSAGE_DB)
        if not tmp:
            logger.warning("enrich_imessage_handles: could not copy chat.db")
            return {"handles_scanned": 0, "matched": 0, "identifiers_added": 0,
                    "already_existed": 0, "skipped_ambiguous": 0}

        try:
            chat_conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            chat_conn.row_factory = sqlite3.Row

            # Read all handles with message counts (1:1 chats only, style=45).
            rows = chat_conn.execute("""
                SELECT h.ROWID, h.id AS handle_id,
                       COUNT(m.ROWID) AS msg_count
                FROM handle h
                LEFT JOIN chat_handle_join chj ON chj.handle_id = h.ROWID
                LEFT JOIN chat c ON chj.chat_id = c.ROWID AND c.style = 45
                LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
                LEFT JOIN message m ON cmj.message_id = m.ROWID
                GROUP BY h.ROWID, h.id
            """).fetchall()
            chat_conn.close()
        except sqlite3.Error as e:
            logger.warning("enrich_imessage_handles: chat.db query failed: %s", e)
            return {"handles_scanned": 0, "matched": 0, "identifiers_added": 0,
                    "already_existed": 0, "skipped_ambiguous": 0}
        finally:
            Path(tmp).unlink(missing_ok=True)
            for ext in ("-wal", "-shm"):
                Path(tmp + ext).unlink(missing_ok=True)

        counts = {
            "handles_scanned": len(rows),
            "matched": 0,
            "identifiers_added": 0,
            "already_existed": 0,
            "skipped_ambiguous": 0,
        }

        # Match handles → person_ids, track message counts per (pid, handle).
        pid_handles: dict[str, list[tuple[str, int]]] = {}  # pid → [(handle, msg_count)]

        for row in rows:
            handle_id = (row["handle_id"] or "").strip()
            if not handle_id:
                continue

            msg_count = row["msg_count"] or 0
            pid = None

            if "@" in handle_id and "whatsapp" not in handle_id.lower():
                # Email-style handle
                pid = email_to_pid.get(handle_id.strip().lower())
            else:
                sfx = _phone_suffix(handle_id)
                if sfx:
                    if sfx in ambiguous_suffixes:
                        counts["skipped_ambiguous"] += 1
                        continue
                    pid = suffix_to_pid.get(sfx)

            if pid:
                counts["matched"] += 1
                pid_handles.setdefault(pid, []).append((handle_id, msg_count))

        # Check existing imessage_handle identifiers.
        existing_imsg: dict[str, set[str]] = {}  # pid → set of handles
        for row in conn.execute(
            "SELECT person_id, value FROM person_identifiers WHERE type = 'imessage_handle'"
        ).fetchall():
            existing_imsg.setdefault(row["person_id"], set()).add(row["value"])

        # Write new identifiers.
        now = int(time.time())
        for pid, handle_list in pid_handles.items():
            # Sort by msg_count desc to determine primary.
            handle_list.sort(key=lambda t: t[1], reverse=True)
            existing_for_pid = existing_imsg.get(pid, set())

            for i, (handle_val, _msg_count) in enumerate(handle_list):
                if handle_val in existing_for_pid:
                    counts["already_existed"] += 1
                    continue

                is_primary = 1 if i == 0 and not existing_for_pid else 0

                if not dry_run:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO person_identifiers "
                            "(person_id, type, value, normalized, is_primary, source, added_at) "
                            "VALUES (?, 'imessage_handle', ?, ?, ?, 'apple-messages-enrichment', ?)",
                            (pid, handle_val, handle_val, is_primary, now),
                        )
                        counts["identifiers_added"] += 1
                    except sqlite3.IntegrityError:
                        counts["already_existed"] += 1
                else:
                    counts["identifiers_added"] += 1

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

    resolved = 0
    batch: list[tuple[str, str]] = []  # (person_id, msg_id)
    for msg in unresolved:
        msg_id = msg[0]
        direction = msg[1]
        sender_id = (msg[2] or "").strip()
        recipient_id = (msg[3] or "").strip()
        channel = msg[4]
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
    import argparse, sys
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

    print("Phase 2: iMessage handles → people.db imessage_handle enrichment")
    imsg_counts = enrich_imessage_handles(dry_run=args.dry_run)
    for k, v in imsg_counts.items():
        print(f"  {k}: {v}")
    print()

    print("Phase 3: Bulk re-resolve comms.db (NULL person_id rows)")
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
