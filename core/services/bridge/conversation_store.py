"""The bridge's own record of what it said and heard (aos#235).

For five months this was `activity_client.py`: four functions that returned
`None`, called on every single message, waiting for a dashboard that had
already been decommissioned. The aos-app activity feed they gestured at is
gone too — the desktop app was retired in migration 118 — so the question
"what did the bridge actually send and receive" had no answer at all. This
module is the answer: one SQLite file the bridge owns, no service, no network,
stdlib only.

    messages(id, ts, direction, chat_id, topic, kind, text_redacted, meta_json,
             status)

`direction` is "in" (operator → system) or "out" (system → operator).
`status` is the one column beyond the declared schema: "sent" for anything that
went out, "queued" for an outbound message parked by quiet hours and
"digested" once a flush has folded it into a digest. It lives here rather than
in a second table because a queued message *is* an outbound message that has
not left yet, and two tables would mean two places to look for the same line.

**Text is redacted on the way in, never on the way out.** Phone numbers and
email addresses are replaced with `[phone]` / `[email]` before the row is
written, so the file cannot become a shadow copy of the operator's contacts.
A store that redacted at read time would be a store that kept the data.

Stdlib only and free of bridge imports on purpose: the bridge imports it as a
module, and `core/engine/notify/router.py` loads it by file path (the same
trick `aos-notify` uses for the router), so one DB serves both paths.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".aos" / "data" / "bridge.db"

# Module-level so a test (or a one-off script) can rebind it. Reads of the
# environment win, so the service and the CLI agree without coordination.
DB_PATH: Path | None = None

# Table first, indexes last, with the one additive column checked in between:
# a store written before `status` existed would fail `CREATE INDEX ... (status)`
# before an ALTER could add it. Migration 124 does the same, in the same order.
TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    direction     TEXT    NOT NULL CHECK (direction IN ('in', 'out')),
    chat_id       INTEGER,
    topic         TEXT,
    kind          TEXT,
    text_redacted TEXT,
    meta_json     TEXT,
    status        TEXT    NOT NULL DEFAULT 'sent'
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (ts);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);
"""

# One row per deduped notice (aos#2324): heartbeat alerts, and anything else
# that wants "don't resend this until it changes or goes stale" semantics,
# without a bespoke JSON file each. `key` identifies what the notice is about
# (a heartbeat alert's own text, currently — see heartbeat.py), `fingerprint`
# is what changed-or-not is judged against, and `last_sent` is when it was
# last actually delivered. This is what survives a bridge restart; the old
# bug was keeping this only in a thread-local set that died with the process.
NOTICE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS notice_state (
    key         TEXT PRIMARY KEY,
    last_sent   TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);
"""

# ── Redaction ───────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Phone-shaped runs of digits and separators. Deliberately wide, then filtered
# in the callback: a regex tight enough to only match real numbers would miss
# the international formats the operator actually types.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{6,17}\d(?![\w.])")

# Things that are digits-and-dashes but are not phone numbers.
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _phone_sub(match: re.Match) -> str:
    raw = match.group(0)
    if _ISO_DATE_RE.search(raw):
        return raw
    digits = re.sub(r"\D", "", raw)
    # With a country code, 8 digits is a real number. Without one, demand 10 —
    # below that we would be redacting port numbers and test counts.
    floor = 8 if raw.lstrip().startswith("+") else 10
    if floor <= len(digits) <= 15:
        return "[phone]"
    return raw


def redact(text: str | None) -> str:
    """Replace phone numbers and email addresses with placeholders."""
    if not text:
        return ""
    out = _EMAIL_RE.sub("[email]", text)
    out = _PHONE_CANDIDATE_RE.sub(_phone_sub, out)
    return out


# ── Storage ─────────────────────────────────────────────────────────────────

def db_path() -> Path:
    """Where the store lives: ``AOS_BRIDGE_DB``, then the module global, then
    the instance default. Resolved per call so a test can redirect it."""
    env = os.environ.get("AOS_BRIDGE_DB")
    if env:
        return Path(env)
    return DB_PATH or DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    """Bring an open connection's schema up to date. Safe to repeat."""
    conn.executescript(TABLE_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "status" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'")
    conn.executescript(INDEX_SQL)
    conn.executescript(NOTICE_TABLE_SQL)


def ensure_schema() -> None:
    """Create the table and indexes if they are not there. Safe to repeat."""
    conn = _connect()
    try:
        _ensure(conn)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def record(direction: str, text: str | None, chat_id: int | None = None,
           topic: str | None = None, kind: str | None = None,
           meta: dict | None = None, status: str = "sent") -> int | None:
    """Write one message row. Returns its id, or None if the store is unusable.

    Never raises: a conversation log that can take down the bridge is worse
    than no conversation log. Failures are swallowed here and surface in
    ``bridge.log`` through the caller's own logging.
    """
    if direction not in ("in", "out"):
        raise ValueError(f"direction must be 'in' or 'out', got {direction!r}")
    try:
        conn = _connect()
    except sqlite3.Error:
        return None
    try:
        _ensure(conn)
        cur = conn.execute(
            "INSERT INTO messages (ts, direction, chat_id, topic, kind, "
            "text_redacted, meta_json, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), direction, chat_id, topic, kind, redact(text),
             json.dumps(meta, default=str) if meta else None, status),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def record_inbound(text: str | None, chat_id: int | None = None,
                   topic: str | None = None, kind: str = "message",
                   meta: dict | None = None) -> int | None:
    """Something the operator sent us."""
    return record("in", text, chat_id=chat_id, topic=topic, kind=kind, meta=meta)


def record_outbound(text: str | None, chat_id: int | None = None,
                    topic: str | None = None, kind: str = "response",
                    meta: dict | None = None, status: str = "sent") -> int | None:
    """Something we sent the operator."""
    return record("out", text, chat_id=chat_id, topic=topic, kind=kind,
                  meta=meta, status=status)


# ── Quiet-hours queue (see core/engine/notify/router.py) ────────────────────

def queue_outbound(text: str | None, topic: str | None = None,
                   kind: str | None = None, meta: dict | None = None) -> int | None:
    """Park a non-urgent outbound message for the next digest."""
    return record("out", text, topic=topic, kind=kind, meta=meta, status="queued")


def queued(limit: int = 50) -> list[sqlite3.Row]:
    """Outbound messages parked by quiet hours, oldest first."""
    try:
        conn = _connect()
    except sqlite3.Error:
        return []
    try:
        _ensure(conn)
        return conn.execute(
            "SELECT * FROM messages WHERE status = 'queued' AND direction = 'out' "
            "ORDER BY id LIMIT ?", (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def mark_digested(ids: list[int]) -> int:
    """Flip queued rows to 'digested' once a flush has delivered them."""
    if not ids:
        return 0
    try:
        conn = _connect()
    except sqlite3.Error:
        return 0
    try:
        _ensure(conn)
        cur = conn.execute(
            f"UPDATE messages SET status = 'digested' WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()
        return cur.rowcount
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


# ── Reading it back ─────────────────────────────────────────────────────────

def recent(limit: int = 20, direction: str | None = None) -> list[sqlite3.Row]:
    """The last N messages, newest first — the question activity.db stopped
    being able to answer in April."""
    try:
        conn = _connect()
    except sqlite3.Error:
        return []
    try:
        _ensure(conn)
        if direction:
            return conn.execute(
                "SELECT * FROM messages WHERE direction = ? ORDER BY id DESC LIMIT ?",
                (direction, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ── Notice dedupe (aos#2324) ─────────────────────────────────────────────────
#
# heartbeat.py's restart-storm bug: `last_reported` lived only in a thread-local
# set, so it died with every restart and every still-open problem got re-sent
# from scratch — 3,234 restarts, 3,234 repeats of the same alert. These two
# functions give any notice-sender a persisted "have I already told them this,
# recently?" that survives the process going away entirely.

def should_send_notice(key: str, fingerprint: str, ttl_seconds: float) -> bool:
    """True if a notice under `key` is due to be (re)sent right now.

    Due when: it has never been recorded, its fingerprint no longer matches
    the last one sent (the underlying condition changed, not just repeated),
    or the last send is older than `ttl_seconds` (so a problem that never
    goes away still gets an occasional reminder instead of going silent
    forever after the first mention).

    Does not itself record anything — call `record_notice_sent` once the send
    actually succeeds, so a delivery failure isn't mistaken for one that went
    out. Fails open (returns True) if the store can't be read: a duplicate
    alert is a nuisance, a swallowed one is the outage this exists to prevent.
    """
    try:
        conn = _connect()
    except sqlite3.Error:
        return True
    try:
        _ensure(conn)
        row = conn.execute(
            "SELECT last_sent, fingerprint FROM notice_state WHERE key = ?", (key,),
        ).fetchone()
        if row is None or row["fingerprint"] != fingerprint:
            return True
        try:
            last_sent = datetime.strptime(row["last_sent"], "%Y-%m-%dT%H:%M:%S")
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - last_sent).total_seconds()
        return age >= ttl_seconds
    except sqlite3.Error:
        return True
    finally:
        conn.close()


def record_notice_sent(key: str, fingerprint: str) -> None:
    """Record that a notice under `key` (with this `fingerprint`) just went
    out, for `should_send_notice` to consult later — including after a
    restart, which is the entire point."""
    try:
        conn = _connect()
    except sqlite3.Error:
        return
    try:
        _ensure(conn)
        conn.execute(
            "INSERT INTO notice_state (key, last_sent, fingerprint) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET last_sent = excluded.last_sent, "
            "fingerprint = excluded.fingerprint",
            (key, _now(), fingerprint),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
