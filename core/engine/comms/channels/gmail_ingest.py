"""Gmail API ingest — the live email pipe for comms.db.

Apple Mail on this headless Mac stopped syncing (Mail.app never runs
foregrounded, so its Envelope Index froze — see the Ambient Knowledge
dossier §email). ``apple_mail_desktop.py`` therefore reads a stale cache.
This module replaces the intake with the Gmail API, pulled through the
existing ``gws`` CLI, and writes into the SAME ``comms.db.messages`` table
with ``channel='email'`` — a new *source* for the existing channel, not a
new channel.

Auth
----
OAuth credentials live one file per account under
``~/.google_workspace_mcp/credentials/<email>.json`` (the Google Workspace
MCP store, operator-managed). Each file carries a ``refresh_token`` and the
shared OAuth client id/secret. We refresh a short-lived access token
(non-interactive server call — NOT the interactive consent flow) and hand
it to ``gws`` via ``GOOGLE_WORKSPACE_CLI_TOKEN``. Accounts are DISCOVERED
from the directory — nothing is hardcoded. An account whose refresh fails
is reported and skipped; we never launch an OAuth GUI flow ourselves.

Dedup (two layers)
------------------
1. Same-source / re-run: message ``id`` is ``gmail:<msg_id>`` and rows go in
   with ``INSERT OR IGNORE``. An incremental watermark (max ``internalDate``
   per account, in ``~/.aos/data/.gmail-ingest-state.json``) avoids
   re-fetching already-seen mail.
2. Cross-source vs the legacy Apple-Mail rows (``em_<rowid>``): those rows
   carry no RFC ``Message-ID`` header, so we match on
   ``(normalized_subject, counterpart_email, timestamp ±1 min)``. This only
   matters in the overlap window (on/before the last Apple-Mail row), so we
   build the Apple key set from that window and skip Gmail rows that collide.

Spam guard
----------
The list query excludes ``in:spam`` / ``in:trash``; as a belt-and-suspenders
step any message still carrying a ``SPAM`` or ``TRASH`` label is skipped at
map time. Label lists are stored in ``channel_metadata`` (no schema change).

Used by
-------
- ``people-intel-refresh`` nightly cron — incremental pull since watermark.
- Run directly for a full backfill:
  ``python3 -m core.engine.comms.channels.gmail_ingest --since 2026-04-01``
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

# Repo root importable when run directly or via -m. This file lives at
# core/engine/comms/channels/ — parents[4] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────

CREDENTIALS_DIR = Path.home() / ".google_workspace_mcp" / "credentials"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
STATE_FILE = Path.home() / ".aos" / "data" / ".gmail-ingest-state.json"

# Body content cap — bytes of extracted text stored per message.
BODY_CAP = 10_000

# Labels that mark a message as spam/trash — never ingested.
_SKIP_LABELS = {"SPAM", "TRASH"}


# ── Stats ────────────────────────────────────────────────────────────────


@dataclass
class IngestStats:
    account: str = ""
    listed: int = 0
    fetched: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_spam: int = 0
    skipped_apple_dup: int = 0
    person_matches: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"account:            {self.account}",
            f"listed:             {self.listed:,}",
            f"fetched:            {self.fetched:,}",
            f"inserted:           {self.inserted:,}",
            f"duplicates (gmail): {self.skipped_existing:,}",
            f"duplicates (apple): {self.skipped_apple_dup:,}",
            f"skipped (spam):     {self.skipped_spam:,}",
            f"person matches:     {self.person_matches:,}",
        ]
        if self.earliest and self.latest:
            lines.append(
                f"date range:         {self.earliest.date()} → {self.latest.date()}"
            )
        if self.errors:
            lines.append(f"errors:             {len(self.errors)}")
        return "\n".join(lines)


# ── State (watermark) ────────────────────────────────────────────────────


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── HTML → text ──────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Collapse HTML to readable plain text (stdlib only)."""

    _BLOCK = {
        "p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "ul", "ol", "blockquote", "section", "article",
    }
    _DROP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._DROP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML — return whatever we parsed
        pass
    return parser.text()


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ── Message parsing (pure) ───────────────────────────────────────────────


def _iter_parts(payload: dict):
    yield payload
    for child in payload.get("parts", []) or []:
        yield from _iter_parts(child)


def extract_body(payload: dict) -> tuple[str, bool]:
    """Return (text, truncated). Prefer text/plain, fall back to text/html."""
    plain: str | None = None
    html: str | None = None
    for part in _iter_parts(payload):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue
        if mime == "text/plain" and plain is None:
            plain = _b64url_decode(data)
        elif mime == "text/html" and html is None:
            html = _html_to_text(_b64url_decode(data))

    text = plain if plain else (html or "")
    text = text.strip()
    if len(text) > BODY_CAP:
        return text[:BODY_CAP], True
    return text, False


def _has_attachment(payload: dict) -> int:
    for part in _iter_parts(payload):
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            return 1
    return 0


def parse_headers(payload: dict) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in payload.get("headers", []) or []
    }


_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def parse_addr(value: str) -> str:
    """Extract the bare email from a ``Name <email>`` header value."""
    if not value:
        return ""
    m = _ADDR_RE.search(value)
    return m.group(0).strip().lower() if m else ""


def parse_addrs(value: str) -> list[str]:
    if not value:
        return []
    return [a.lower() for a in _ADDR_RE.findall(value)]


def _ts_from_internal_date(internal_date: str | int) -> datetime | None:
    """Gmail internalDate is epoch milliseconds. Mirror the Apple adapter's
    naive-local convention so overlap-window dedup compares like-for-like."""
    try:
        return datetime.fromtimestamp(int(internal_date) / 1000)
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def normalize_subject(subject: str) -> str:
    s = (subject or "").lower().strip()
    while True:
        new = re.sub(r"^(re|fwd|fw|aw)\s*:\s*", "", s)
        if new == s:
            break
        s = new
    return re.sub(r"\s+", " ", s).strip()


def map_message(
    raw: dict,
    account: str,
    self_accounts: set[str],
    addr_to_person: dict[str, str] | None = None,
) -> dict | None:
    """Map a Gmail ``messages.get`` (format=full) response to a comms row.

    Returns ``None`` when there is no usable timestamp or address. Spam/trash
    filtering is the caller's job via :func:`is_spam` (checked before this),
    not a ``None`` return here.
    """
    labels = raw.get("labelIds", []) or []
    payload = raw.get("payload", {}) or {}
    headers = parse_headers(payload)

    ts = _ts_from_internal_date(raw.get("internalDate"))
    if ts is None:
        return None

    sender = parse_addr(headers.get("from", ""))
    to_addrs = parse_addrs(headers.get("to", ""))
    cc_addrs = parse_addrs(headers.get("cc", ""))
    primary_to = to_addrs[0] if to_addrs else ""

    is_outbound = bool(sender) and sender in self_accounts
    if is_outbound:
        direction = "outbound"
        sender_id = "me"
        recipient_id = primary_to
        counterpart = primary_to
    else:
        direction = "inbound"
        sender_id = sender
        recipient_id = "me"
        counterpart = sender

    if not (sender or primary_to):
        return None

    subject = headers.get("subject", "")
    body, truncated = extract_body(payload)
    content = f"{subject}\n\n{body}".strip() if body else subject

    msg_id = raw.get("id", "")
    thread_id = raw.get("threadId", "") or None
    conv_id = f"conv_gmail_{thread_id}" if thread_id else f"conv_gmail_msg_{msg_id}"

    person_id = None
    if addr_to_person:
        person_id = addr_to_person.get(counterpart)

    channel_meta = {
        "source": "gmail",
        "account": account,
        "gmail_id": msg_id,
        "thread_id": thread_id,
        "labels": labels,
        "subject": subject,
        "message_id_header": headers.get("message-id", ""),
        "from": sender,
        "to": to_addrs,
        "cc": cc_addrs,
        "snippet": raw.get("snippet", ""),
        "truncated": truncated,
    }

    return {
        "id": f"gmail:{msg_id}",
        "channel": "email",
        "direction": direction,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "content": content,
        "timestamp": ts.isoformat(),
        "thread_id": thread_id,
        "has_attachment": _has_attachment(payload),
        "attachment_type": None,
        "attachment_path": None,
        "channel_metadata": json.dumps(channel_meta, ensure_ascii=False),
        "person_id": person_id,
        "conversation_id": conv_id,
        "_ts": ts,
        "_internal_date": int(raw.get("internalDate", 0) or 0),
        "_counterpart": counterpart,
        "_subject_norm": normalize_subject(subject),
        "_labels": labels,
    }


def is_spam(raw: dict) -> bool:
    return bool(set(raw.get("labelIds", []) or []) & _SKIP_LABELS)


# ── Cross-source (Apple) dedup ───────────────────────────────────────────


def _apple_dup_index(conn: sqlite3.Connection, until: datetime) -> set[tuple]:
    """Build a dedup key set from legacy Apple-Mail rows on/before ``until``.

    Key: (normalized_subject, counterpart_email, epoch_minute). Apple rows
    carry no Message-ID, so this fuzzy key is the only cross-source match.
    """
    index: set[tuple] = set()
    rows = conn.execute(
        "SELECT direction, sender_id, recipient_id, timestamp, channel_metadata "
        "FROM messages WHERE channel='email' AND id LIKE 'em_%' "
        "AND timestamp <= ?",
        (until.isoformat(),),
    ).fetchall()
    for direction, sender_id, recipient_id, timestamp, meta_json in rows:
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        subject = normalize_subject(meta.get("subject", ""))
        if direction == "outbound":
            to = meta.get("to") or []
            counterpart = (to[0] if to else recipient_id or "").lower()
        else:
            counterpart = (meta.get("sender_address") or sender_id or "").lower()
        try:
            minute = int(datetime.fromisoformat(timestamp).timestamp() // 60)
        except (ValueError, TypeError):
            continue
        index.add((subject, counterpart, minute))
    return index


def _is_apple_dup(row: dict, index: set[tuple]) -> bool:
    subject = row["_subject_norm"]
    counterpart = (row["_counterpart"] or "").lower()
    minute = int(row["_ts"].timestamp() // 60)
    for m in (minute - 1, minute, minute + 1):
        if (subject, counterpart, m) in index:
            return True
    return False


# ── Person resolution ────────────────────────────────────────────────────


def _build_email_person_map(
    addresses: set[str], people_conn: sqlite3.Connection
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    addrs = [a for a in addresses if a and "@" in a]
    CHUNK = 500
    for i in range(0, len(addrs), CHUNK):
        chunk = addrs[i : i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = people_conn.execute(
            f"SELECT normalized, person_id FROM person_identifiers "
            f"WHERE type='email' AND normalized IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for normalized, person_id in rows:
            if normalized:
                mapping[normalized.lower()] = person_id
    return mapping


# ── Gmail client (gws) ───────────────────────────────────────────────────


def refresh_access_token(cred: dict) -> str:
    """Exchange a refresh token for a short-lived access token.

    Non-interactive server-to-server call — NOT the OAuth consent GUI.
    """
    data = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    uri = cred.get("token_uri", "https://oauth2.googleapis.com/token")
    req = urllib.request.Request(uri, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


class GwsGmail:
    """Thin Gmail wrapper over the ``gws`` CLI for one account.

    ``runner`` is injectable for tests: a callable ``(args) -> (rc, out, err)``.
    """

    def __init__(self, account: str, cred: dict, runner=None):
        self.account = account
        self.cred = cred
        self.token = refresh_access_token(cred) if runner is None else "test-token"
        self._runner = runner or self._default_runner

    def _default_runner(self, args: list[str]) -> tuple[int, str, str]:
        env = dict(os.environ, GOOGLE_WORKSPACE_CLI_TOKEN=self.token)
        p = subprocess.run(
            ["gws", *args], env=env, capture_output=True, text=True
        )
        return p.returncode, p.stdout, p.stderr

    def _call(self, args: list[str]) -> dict:
        rc, out, err = self._runner(args)
        if rc != 0 or not out.strip():
            # Token may have expired mid-backfill — refresh once and retry.
            if "401" in err or "invalid" in err.lower() or "unauthorized" in err.lower():
                self.token = refresh_access_token(self.cred)
                rc, out, err = self._runner(args)
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(f"gws returned non-JSON (rc={rc}): {err[:200]}")

    def list_ids(self, query: str, max_pages: int = 100) -> list[str]:
        rc, out, err = self._runner([
            "gmail", "users", "messages", "list",
            "--params", json.dumps({"userId": "me", "q": query, "maxResults": 500}),
            "--page-all", "--page-limit", str(max_pages),
        ])
        if rc != 0:
            raise RuntimeError(f"gmail list failed: {err[:200]}")
        ids: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                page = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            for m in page.get("messages", []) or []:
                if m.get("id"):
                    ids.append(m["id"])
        return ids

    def get(self, msg_id: str) -> dict:
        return self._call([
            "gmail", "users", "messages", "get",
            "--params", json.dumps({"userId": "me", "id": msg_id, "format": "full"}),
        ])


# ── Ingest one account ───────────────────────────────────────────────────


def _build_query(since: datetime | None, watermark_ms: int | None) -> str:
    parts = ["-in:spam", "-in:trash"]
    cutoff = None
    if watermark_ms:
        # Small 60s safety overlap; INSERT OR IGNORE handles the re-seen rows.
        cutoff = max(0, watermark_ms // 1000 - 60)
    if since is not None:
        since_epoch = int(since.timestamp())
        cutoff = since_epoch if cutoff is None else min(cutoff, since_epoch)
    if cutoff is not None:
        parts.append(f"after:{cutoff}")
    return " ".join(parts)


def ingest_account(
    account: str,
    cred: dict,
    *,
    since: datetime | None = None,
    client: GwsGmail | None = None,
    comms_db: Path = COMMS_DB,
    people_db: Path = PEOPLE_DB,
    self_accounts: set[str] | None = None,
    state: dict | None = None,
    dry_run: bool = False,
    default_days: int = 30,
    progress_cb=None,
) -> IngestStats:
    stats = IngestStats(account=account)
    state = _load_state() if state is None else state
    # "Self" = the operator's own mailbox addresses, derived from the
    # discovered credential files (never hardcoded). From-address in this set
    # ⇒ outbound. main() passes the full set; a lone-account call defaults to
    # just this account, which still classifies its own Sent mail correctly.
    self_accounts = self_accounts or {account}

    if client is None:
        client = GwsGmail(account, cred)

    watermark_ms = state.get(account, {}).get("watermark_ms")
    # Fresh account, no explicit backfill window: bound the pull to the last
    # ``default_days`` rather than fetching all-time history on a nightly run.
    if since is None and watermark_ms is None:
        since = datetime.now() - timedelta(days=default_days)
    query = _build_query(since, None if since else watermark_ms)

    try:
        ids = client.list_ids(query)
    except Exception as e:
        stats.errors.append(f"list: {e}")
        return stats
    stats.listed = len(ids)
    if not ids:
        return stats

    conn = sqlite3.connect(str(comms_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Which of these are already stored (layer-a dedup, precise).
    existing: set[str] = set()
    gmail_ids = [f"gmail:{i}" for i in ids]
    CHUNK = 500
    for i in range(0, len(gmail_ids), CHUNK):
        chunk = gmail_ids[i : i + CHUNK]
        ph = ",".join("?" * len(chunk))
        for (rid,) in conn.execute(
            f"SELECT id FROM messages WHERE id IN ({ph})", chunk
        ):
            existing.add(rid)

    to_fetch = [i for i in ids if f"gmail:{i}" not in existing]
    stats.skipped_existing = len(ids) - len(to_fetch)

    # Apple overlap index — only built over the window Apple actually covered.
    apple_until = None
    row = conn.execute(
        "SELECT MAX(timestamp) FROM messages WHERE channel='email' AND id LIKE 'em_%'"
    ).fetchone()
    if row and row[0]:
        try:
            apple_until = datetime.fromisoformat(row[0])
        except (ValueError, TypeError):
            apple_until = None
    apple_index = (
        _apple_dup_index(conn, apple_until) if apple_until else set()
    )

    # Resolve counterpart emails → person_id.
    addr_to_person: dict[str, str] = {}

    rows_to_insert: list[dict] = []
    max_internal = watermark_ms or 0
    for n, mid in enumerate(to_fetch):
        try:
            raw = client.get(mid)
        except Exception as e:
            stats.errors.append(f"get {mid}: {e}")
            continue
        stats.fetched += 1
        max_internal = max(max_internal, int(raw.get("internalDate", 0) or 0))

        if is_spam(raw):
            stats.skipped_spam += 1
            continue
        mapped = map_message(raw, account, self_accounts)
        if mapped is None:
            continue
        if apple_index and mapped["_ts"] <= (apple_until or mapped["_ts"]):
            if _is_apple_dup(mapped, apple_index):
                stats.skipped_apple_dup += 1
                continue
        rows_to_insert.append(mapped)
        if progress_cb and n and n % 200 == 0:
            progress_cb(n, len(to_fetch))

    # Resolve people for all counterparts in one shot.
    if people_db.exists() and rows_to_insert:
        addrs = {r["_counterpart"] for r in rows_to_insert if r["_counterpart"]}
        pconn = sqlite3.connect(str(people_db))
        try:
            addr_to_person = _build_email_person_map(addrs, pconn)
        finally:
            pconn.close()

    if rows_to_insert:
        stats.earliest = min(r["_ts"] for r in rows_to_insert)
        stats.latest = max(r["_ts"] for r in rows_to_insert)

    if dry_run:
        conn.close()
        return stats

    batch = []
    for r in rows_to_insert:
        person_id = r["person_id"] or addr_to_person.get(r["_counterpart"])
        if person_id:
            stats.person_matches += 1
        batch.append((
            r["id"], r["channel"], r["direction"], r["sender_id"],
            r["recipient_id"], r["content"], r["timestamp"], r["thread_id"],
            r["has_attachment"], r["attachment_type"], r["attachment_path"],
            r["channel_metadata"], person_id, r["conversation_id"],
        ))

    INSERT_SQL = """
        INSERT OR IGNORE INTO messages
          (id, channel, direction, sender_id, recipient_id, content,
           timestamp, thread_id, has_attachment, attachment_type,
           attachment_path, channel_metadata, person_id, conversation_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    try:
        for i in range(0, len(batch), CHUNK):
            conn.executemany(INSERT_SQL, batch[i : i + CHUNK])
        conn.commit()
        # ``batch`` already excludes rows that existed, were spam, or matched
        # an Apple row, so its length is the count of genuinely new rows. Do
        # NOT use conn.total_changes — the messages_fts triggers fire per row
        # and would inflate the count several-fold.
        stats.inserted = len(batch)
    except Exception as e:
        conn.rollback()
        stats.errors.append(f"insert: {e}")
        conn.close()
        raise
    finally:
        conn.close()

    # Advance the watermark only after a successful commit.
    if max_internal and not dry_run:
        state.setdefault(account, {})["watermark_ms"] = max_internal
        state[account]["updated"] = datetime.now().isoformat()

    return stats


# ── Account discovery ────────────────────────────────────────────────────


def discover_accounts() -> list[tuple[str, dict]]:
    """Return (email, cred_dict) for every credential file on disk."""
    out: list[tuple[str, dict]] = []
    if not CREDENTIALS_DIR.exists():
        return out
    for path in sorted(CREDENTIALS_DIR.glob("*.json")):
        try:
            cred = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "refresh_token" in cred and "client_id" in cred:
            out.append((path.stem, cred))
    return out


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest Gmail into comms.db via the gws CLI"
    )
    parser.add_argument("--since", type=str, default=None,
                        help="Backfill from this date (YYYY-MM-DD), ignoring watermark")
    parser.add_argument("--days", type=int, default=None,
                        help="Incremental: pull the last N days (ignores watermark)")
    parser.add_argument("--account", type=str, default=None,
                        help="Only ingest this account (default: all discovered)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and map but do not write")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since)
    elif args.days:
        since = datetime.now() - timedelta(days=args.days)

    accounts = discover_accounts()
    if args.account:
        accounts = [(a, c) for a, c in accounts if a == args.account]
    if not accounts:
        print("error: no usable credential files in "
              f"{CREDENTIALS_DIR}", file=sys.stderr)
        return 2

    all_emails = {a for a, _ in accounts}
    state = _load_state()

    print(f"Accounts discovered: {', '.join(a for a, _ in accounts)}")
    if since:
        print(f"Backfill window: since {since.date()}")

    any_ok = False
    any_fail = False
    for account, cred in accounts:
        print(f"\n── {account} ──")
        try:
            stats = ingest_account(
                account, cred, since=since, self_accounts=all_emails,
                state=state, dry_run=args.dry_run,
            )
        except Exception as e:  # auth/network failure — report, keep going
            print(f"  FAILED: {e}", file=sys.stderr)
            any_fail = True
            continue
        print(stats.summary())
        if stats.errors:
            any_fail = True
            for err in stats.errors[:5]:
                print(f"  error: {err}", file=sys.stderr)
        else:
            any_ok = True
        # Persist the watermark after each account so a failure on a later
        # (large) account doesn't lose the progress of the ones that finished.
        if not args.dry_run:
            _save_state(state)

    # Honest exit code: nonzero if any account errored (mirrors the cron's
    # partial-failure convention — all accounts still ran first).
    return 0 if any_ok and not any_fail else (1 if any_fail else 0)


if __name__ == "__main__":
    sys.exit(main())
