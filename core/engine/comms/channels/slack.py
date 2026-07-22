"""Slack history ingest — pulls Slack conversation history into comms.db.

A new *channel* (``channel='slack'``) for the comms substrate, mirroring the
two remote/local ingest siblings: ``gmail_ingest.py`` (remote paginated API,
per-scope auth, watermark backfill) and ``imessage_desktop.py`` (deterministic
ids, INSERT OR IGNORE idempotency, conversation upserts). Like them it writes
into the SAME ``comms.db.messages`` / ``conversations`` tables and is safe to
re-run.

Auth
----
A Slack token is read from the macOS Keychain via the ``agent-secret`` CLI —
``SLACK_USER_TOKEN`` (a user token, ``xoxp-…``) preferred, falling back to
``SLACK_BOT_TOKEN`` (a bot token, ``xoxb-…``). A user token sees the operator's
own DMs and private channels; a bot token only sees what the bot is a member
of. Tokens are never printed or logged. No token in the Keychain ⇒ the adapter
prints a one-line "not configured" note and exits 0 (skip, not crash).

Graceful degradation (component-lifecycle rule)
----------------------------------------------
Slack tokens carry granular scopes. If the token lacks the read scopes this
adapter needs, Slack answers ``{ok: false, error: "missing_scope"}``. Rather
than crash, the adapter prints the exact scopes to add (see ``REQUIRED_SCOPES``)
and exits 0 — so the nightly cron stays green while scope expansion happens in
parallel, and the same code lights up the moment the scopes exist. ``auth.test``
needs no scope, so identity resolution always succeeds if the token is valid.

Dedup / incremental
-------------------
Message ``id`` is ``slack_<channel_id>_<ts>`` (a Slack ``ts`` is unique within
its channel), inserted with ``INSERT OR IGNORE`` — re-runs are no-ops. The
incremental watermark is ``MAX(timestamp)`` over existing ``channel='slack'``
rows (same idea as ``gmail_ingest``'s backfill window, read straight from the
DB rather than a side-car state file). A small safety overlap re-requests the
last minute; the OR IGNORE swallows the re-seen rows.

Rate limits
-----------
The Slack Web API is tiered (~1 req/sec for the methods used here). The default
transport paces requests to one per second and honours ``Retry-After`` on HTTP
429. All calls are read-only — this adapter never posts to Slack.

Used by
-------
- ``people-intel-refresh`` nightly cron — incremental pull.
- Run directly for a backfill / smoke test:
  ``python3 -m core.engine.comms.channels.slack --dry-run --days 7``
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# Make the repo root importable when run directly as a script or via -m.
# This file lives at core/engine/comms/channels/ — parents[4] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

# Slack Web API base. Only ever hit with read methods (see below).
SLACK_API = "https://slack.com/api"

# Conversation types requested from conversations.list.
CONV_TYPES = "public_channel,private_channel,im,mpim"

# The read scopes this adapter needs. Printed verbatim when Slack answers
# missing_scope so the operator knows exactly what to add to the Slack app.
REQUIRED_SCOPES = [
    "channels:read", "groups:read", "im:read", "mpim:read",
    "channels:history", "groups:history", "im:history", "mpim:history",
    "users:read",
]

# Body content cap — bytes of message text stored per row.
BODY_CAP = 10_000

# Re-request the last minute on every incremental run; INSERT OR IGNORE
# absorbs the overlap. Guards against clock skew / boundary races.
WATERMARK_OVERLAP_S = 60

# Message subtypes that are channel bookkeeping, not real conversation —
# skipped at map time (mirrors the system-message filter in the iMessage
# adapter). Normal messages have no subtype; thread replies and bot messages
# with text are kept.
_SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive",
    "group_join", "group_leave", "group_topic", "group_purpose",
    "group_name", "group_archive", "group_unarchive",
    "bot_add", "bot_remove", "pinned_item", "unpinned_item",
}


# ── Errors ───────────────────────────────────────────────────────────────


class MissingScopeError(Exception):
    """Raised when Slack rejects a call for lack of an OAuth scope.

    Caught at the top of :func:`ingest` and turned into an actionable skip —
    never a crash. ``needed``/``provided`` come straight from the Slack error
    payload; ``method`` is the Web API method that was refused.
    """

    def __init__(self, method: str, needed: str = "", provided: str = ""):
        self.method = method
        self.needed = needed
        self.provided = provided
        super().__init__(
            f"{method}: missing_scope (needed={needed or '?'})"
        )


class SlackAPIError(Exception):
    """A non-scope Slack ``ok:false`` error, or an HTTP/transport failure."""


# ── Stats container ──────────────────────────────────────────────────────


@dataclass
class IngestStats:
    team: str = ""
    self_user: str = ""
    channels_scanned: int = 0
    total_scanned: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_no_text: int = 0
    conversations_created: int = 0
    conversations_updated: int = 0
    skipped_no_scope: bool = False
    earliest: datetime | None = None
    latest: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped_no_scope:
            return "skipped: Slack token lacks the required read scopes (see above)"
        lines = [
            f"team:              {self.team}",
            f"channels scanned:  {self.channels_scanned:,}",
            f"scanned:           {self.total_scanned:,}",
            f"inserted:          {self.inserted:,}",
            f"duplicates:        {self.skipped_existing:,}",
            f"skipped (no text): {self.skipped_no_text:,}",
            f"conversations (new/updated): {self.conversations_created} / {self.conversations_updated}",
        ]
        if self.earliest and self.latest:
            lines.append(f"date range:        {self.earliest.date()} → {self.latest.date()}")
        if self.errors:
            lines.append(f"errors:            {len(self.errors)}")
        return "\n".join(lines)


# ── Auth ─────────────────────────────────────────────────────────────────


def get_token() -> str | None:
    """Return a Slack token from the Keychain, or ``None`` if unconfigured.

    Prefers the user token (``xoxp``, wider read surface) over the bot token
    (``xoxb``). Never logs the value.
    """
    for name in ("SLACK_USER_TOKEN", "SLACK_BOT_TOKEN"):
        try:
            out = subprocess.run(
                [str(AGENT_SECRET), "get", name],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        token = (out.stdout or "").strip()
        if out.returncode == 0 and token:
            return token
    return None


# ── Slack Web API client (stdlib urllib) ─────────────────────────────────


class SlackClient:
    """Thin, rate-limit-aware Slack Web API client over stdlib urllib.

    ``transport`` is injectable for tests: a callable
    ``(method: str, params: dict) -> dict`` returning the parsed Slack JSON
    body (with its own ``ok``/``error`` fields). The default transport does the
    real HTTP GET, ~1 req/sec pacing, and ``Retry-After`` handling. Tests pass a
    fake transport and never touch the network or sleep.
    """

    def __init__(self, token: str, transport=None, min_interval: float = 1.0):
        self._token = token
        self._transport = transport or self._http_transport
        self._min_interval = min_interval
        self._last_call = 0.0

    # -- transport ---------------------------------------------------------

    def _http_transport(self, method: str, params: dict) -> dict:
        # Tier-safe pacing: at most one request per ``min_interval`` seconds.
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

        url = f"{SLACK_API}/{method}"
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            url,
            data=data,  # POST form body — keeps the token out of the URL/logs
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", "1") or "1")
                time.sleep(max(1, retry_after))
                return self._http_transport(method, params)
            raise SlackAPIError(f"{method}: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SlackAPIError(f"{method}: {e.reason}") from e
        finally:
            self._last_call = time.monotonic()

        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            raise SlackAPIError(f"{method}: non-JSON response") from e

    # -- one call ----------------------------------------------------------

    def call(self, method: str, **params) -> dict:
        """Invoke a Web API method, raising on Slack-level errors.

        ``missing_scope`` (and the equivalent ``not_allowed_token_type``) become
        :class:`MissingScopeError`; a ``ratelimited`` body is retried once after
        its ``Retry-After``; any other ``ok:false`` is a :class:`SlackAPIError`.
        """
        body = self._transport(method, params)
        if body.get("ok"):
            return body

        error = body.get("error", "unknown")
        if error in ("missing_scope", "not_allowed_token_type"):
            raise MissingScopeError(
                method,
                needed=body.get("needed", ""),
                provided=body.get("provided", ""),
            )
        if error == "ratelimited":
            time.sleep(int(body.get("retry_after", 1) or 1))
            body = self._transport(method, params)
            if body.get("ok"):
                return body
            error = body.get("error", "unknown")
        raise SlackAPIError(f"{method}: {error}")

    # -- cursor pagination -------------------------------------------------

    def paginate(self, method: str, key: str, **params):
        """Yield items under ``key`` across cursor-paginated pages."""
        cursor = ""
        while True:
            call_params = dict(params)
            if cursor:
                call_params["cursor"] = cursor
            body = self.call(method, **call_params)
            for item in body.get(key, []) or []:
                yield item
            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break

    # -- typed helpers -----------------------------------------------------

    def auth_test(self) -> dict:
        """Identity probe. Needs no scope — works with any valid token."""
        return self.call("auth.test")

    def users_map(self) -> dict[str, str]:
        """Return ``{user_id: display_name}`` for the workspace (cached by run)."""
        mapping: dict[str, str] = {}
        for u in self.paginate("users.list", "members", limit=200):
            uid = u.get("id")
            if not uid:
                continue
            profile = u.get("profile") or {}
            mapping[uid] = (
                profile.get("display_name")
                or profile.get("real_name")
                or u.get("real_name")
                or u.get("name")
                or uid
            )
        return mapping

    def conversations(self, channel: str | None = None):
        """List conversations (channels, DMs, group DMs) the token can see.

        ``channel`` optionally restricts to a single conversation id or name.
        """
        for c in self.paginate(
            "conversations.list", "channels",
            types=CONV_TYPES, exclude_archived="true", limit=200,
        ):
            if channel and channel not in (c.get("id"), c.get("name")):
                continue
            yield c

    def history(self, channel_id: str, oldest: float | None = None, limit: int = 200):
        """Yield messages in a channel, oldest-watermark bounded, paginated."""
        params: dict = {"channel": channel_id, "limit": limit}
        if oldest is not None:
            params["oldest"] = f"{oldest:.6f}"
        yield from self.paginate("conversations.history", "messages", **params)


# ── Pure mapping helpers ─────────────────────────────────────────────────


def _ts_to_iso(ts: str | float) -> str | None:
    """Slack ``ts`` (``"1721577600.001200"``) → naive-local ISO string.

    Mirrors the gmail/imessage adapters' naive-local convention so all rows in
    comms.db compare like-for-like.
    """
    try:
        return datetime.fromtimestamp(float(ts)).isoformat()
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def channel_type_of(conv: dict) -> str:
    """Classify a conversations.list entry to one of the four Slack types."""
    if conv.get("is_im"):
        return "im"
    if conv.get("is_mpim"):
        return "mpim"
    if conv.get("is_private") or conv.get("is_group"):
        return "private_channel"
    return "public_channel"


def conversation_name(conv: dict, users: dict[str, str]) -> str:
    """Human name for a conversation: channel name, or DM counterpart username."""
    ctype = channel_type_of(conv)
    if ctype == "im":
        counterpart = conv.get("user", "")
        return users.get(counterpart, counterpart) or "Direct message"
    return conv.get("name") or conv.get("name_normalized") or conv.get("id", "")


def map_message(
    raw: dict,
    conv: dict,
    self_user: str,
    team: str,
    users: dict[str, str] | None = None,
) -> dict | None:
    """Map one Slack ``conversations.history`` message to a comms.db row.

    Returns ``None`` for non-messages, bookkeeping subtypes, or rows with no
    text and no attachment (nothing to store).
    """
    if raw.get("type") not in (None, "message"):
        return None
    if raw.get("subtype") in _SKIP_SUBTYPES:
        return None

    ts = raw.get("ts")
    iso = _ts_to_iso(ts) if ts else None
    if iso is None:
        return None

    files = raw.get("files") or []
    attachments = raw.get("attachments") or []
    has_attachment = 1 if (files or attachments) else 0

    text = (raw.get("text") or "").strip()
    if not text and not has_attachment:
        return None
    if len(text) > BODY_CAP:
        text = text[:BODY_CAP]

    channel_id = conv.get("id", "")
    # Sender: a real user id, or a bot id for app/integration messages.
    sender = raw.get("user") or raw.get("bot_id") or "unknown"
    direction = "outgoing" if sender == self_user else "incoming"

    thread_ts = raw.get("thread_ts")
    # A message whose thread_ts equals its own ts is the thread parent, not a
    # reply — leave thread_id null there so only true replies carry a thread.
    thread_id = thread_ts if (thread_ts and thread_ts != ts) else None

    ctype = channel_type_of(conv)
    attachment_type = None
    if files:
        attachment_type = files[0].get("mimetype") or files[0].get("filetype")

    channel_meta = {
        "source": "slack",
        "team": team,
        "channel_id": channel_id,
        "channel_name": conversation_name(conv, users or {}),
        "channel_type": ctype,
        "ts": ts,
        "thread_ts": thread_ts,
        "user": raw.get("user"),
        "bot_id": raw.get("bot_id"),
        "subtype": raw.get("subtype"),
    }

    return {
        "id": f"slack_{channel_id}_{ts}",
        "channel": "slack",
        "direction": direction,
        "sender_id": sender,
        "recipient_id": channel_id,
        "content": text,
        "timestamp": iso,
        "thread_id": thread_id,
        "has_attachment": has_attachment,
        "attachment_type": attachment_type,
        "attachment_path": None,
        "channel_metadata": json.dumps(channel_meta, ensure_ascii=False),
        "person_id": None,
        "conversation_id": f"slack_{channel_id}",
        # Internal fields used by ingest() aggregation.
        "_ts_iso": iso,
        "_channel_id": channel_id,
    }


# ── Schema bootstrap ─────────────────────────────────────────────────────


def _ensure_base_schema(conn) -> None:
    """Create the comms base schema if it is missing — self-bootstrapping.

    The messages/conversations/messages_fts tables and their FTS sync triggers
    exist in NO migration (they were created ad-hoc on the origin machine), so
    a fresh fleet install has no such tables and any ingest crashes with
    ``no such table: conversations``. This function creates them idempotently at
    ingest start, DDL copied verbatim from the live store, so the adapter boots
    on any machine. Offered in the PR as the pattern to lift into a real
    migration.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          channel TEXT NOT NULL,
          direction TEXT,
          sender_id TEXT,
          recipient_id TEXT,
          content TEXT,
          timestamp TEXT,
          thread_id TEXT,
          has_attachment INTEGER DEFAULT 0,
          attachment_type TEXT,
          attachment_path TEXT,
          channel_metadata TEXT,
          person_id TEXT,
          conversation_id TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY,
          channel TEXT NOT NULL,
          person_id TEXT,
          name TEXT,
          status TEXT DEFAULT 'open',
          last_message_at TEXT,
          message_count INTEGER DEFAULT 0,
          unread_count INTEGER DEFAULT 0,
          metadata TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
        CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id);
        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel);
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
          content, content='messages', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
          INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
          INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        """
    )
    conn.commit()


def _watermark_epoch(conn) -> float | None:
    """Return the incremental floor: MAX(timestamp) over existing slack rows.

    Read straight from comms.db (same idea as gmail_ingest's backfill window).
    Returns the epoch seconds of the newest stored Slack message, or ``None``
    when there are none yet (first run / fresh DB).
    """
    row = conn.execute(
        "SELECT MAX(timestamp) FROM messages WHERE channel='slack'"
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0]).timestamp()
    except (ValueError, TypeError):
        return None


def _resolve_oldest(
    conn,
    since: datetime | None,
    default_days: int,
) -> float | None:
    """Compute the ``oldest`` history floor (epoch seconds), or ``None``.

    Precedence (documented in the module header):
      * ``--since`` given  → that date, widened below any watermark (backfill);
      * else watermark set → the watermark minus a safety overlap (cheap
        incremental — only genuinely new messages);
      * else (fresh DB)    → now minus ``default_days``.
    """
    watermark = _watermark_epoch(conn)
    if since is not None:
        floor = since.timestamp()
        return min(floor, watermark) if watermark else floor
    if watermark is not None:
        return max(0.0, watermark - WATERMARK_OVERLAP_S)
    return (datetime.now() - timedelta(days=default_days)).timestamp()


# ── Conversation aggregation ─────────────────────────────────────────────


def _build_conversations(
    rows: list[dict], convs: dict[str, dict], users: dict[str, str]
) -> dict[str, dict]:
    """Aggregate mapped rows into conversation upsert records."""
    out: dict[str, dict] = {}
    for m in rows:
        cid = m["conversation_id"]
        source = convs.get(m["_channel_id"], {})
        if cid not in out:
            out[cid] = {
                "id": cid,
                "channel": "slack",
                "name": conversation_name(source, users) if source else m["_channel_id"],
                "channel_type": channel_type_of(source) if source else "",
                "last_message_at": m["_ts_iso"],
                "message_count": 0,
            }
        c = out[cid]
        c["message_count"] += 1
        if m["_ts_iso"] > c["last_message_at"]:
            c["last_message_at"] = m["_ts_iso"]
    return out


# ── Ingest ───────────────────────────────────────────────────────────────


def ingest(
    since: datetime | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    channel: str | None = None,
    default_days: int = 14,
    comms_db: Path = COMMS_DB,
    client: SlackClient | None = None,
    progress_cb=None,
) -> IngestStats:
    """Pull Slack history and persist to comms.db.

    Args:
        since:   Only ingest messages on/after this datetime (backfill floor).
        limit:   Hard cap on messages scanned per channel (testing).
        dry_run: Compute stats without writing.
        channel: Restrict to a single channel id or name.
        default_days: Fresh-DB backfill window when there is no watermark.
        comms_db: Target DB (overridable for tests).
        client:  Injected SlackClient (tests); built from the Keychain if None.
        progress_cb: Optional ``cb(phase, current, total)`` for UI updates.
    """
    stats = IngestStats()

    if client is None:
        token = get_token()
        if not token:
            print(
                "Slack: no SLACK_USER_TOKEN / SLACK_BOT_TOKEN in Keychain — "
                "skipping (set one via agent-secret to enable)."
            )
            return stats
        client = SlackClient(token)

    # Identity first — auth.test needs no scope, so this works with any valid
    # token and gives us the "self" id used for direction attribution.
    try:
        auth = client.auth_test()
    except MissingScopeError:
        # Extremely unlikely for auth.test, but handle uniformly.
        _print_scope_help(stats)
        return stats
    except SlackAPIError as e:
        print(f"Slack: auth failed ({e}) — skipping.")
        stats.errors.append(str(e))
        return stats

    stats.self_user = auth.get("user_id", "")
    stats.team = auth.get("team", "")
    team_id = auth.get("team_id", "")

    # Everything past auth.test needs read scopes. A single missing_scope
    # anywhere in the read path ⇒ graceful skip with actionable guidance.
    try:
        users = client.users_map()
        convs = {c["id"]: c for c in client.conversations(channel=channel) if c.get("id")}
    except MissingScopeError as e:
        _print_scope_help(stats, e)
        return stats
    except SlackAPIError as e:
        print(f"Slack: {e} — skipping.")
        stats.errors.append(str(e))
        return stats

    stats.channels_scanned = len(convs)
    if not convs:
        return stats

    # Incremental floor from the DB watermark (read-only open first).
    read_conn = _connect(comms_db)
    try:
        _ensure_base_schema(read_conn)
        oldest = _resolve_oldest(read_conn, since, default_days)
    finally:
        read_conn.close()

    mapped: list[dict] = []
    for i, (cid, conv) in enumerate(convs.items()):
        if progress_cb:
            progress_cb("history", i, len(convs))
        try:
            count = 0
            for raw in client.history(cid, oldest=oldest):
                row = map_message(raw, conv, stats.self_user, team_id, users)
                if row is None:
                    stats.skipped_no_text += 1
                    continue
                mapped.append(row)
                count += 1
                if limit and count >= limit:
                    break
        except MissingScopeError as e:
            # A DM/private-channel history scope may be missing while public
            # ones exist — surface it and stop cleanly rather than half-ingest.
            _print_scope_help(stats, e)
            return stats
        except SlackAPIError as e:
            stats.errors.append(f"history {cid}: {e}")
            continue

    stats.total_scanned = len(mapped)
    if not mapped:
        return stats

    stats.earliest = min(datetime.fromisoformat(m["_ts_iso"]) for m in mapped)
    stats.latest = max(datetime.fromisoformat(m["_ts_iso"]) for m in mapped)

    conversations = _build_conversations(mapped, convs, users)

    if dry_run:
        logger.info(
            "Dry run: would persist %d messages across %d conversations",
            stats.total_scanned, len(conversations),
        )
        return stats

    _persist(comms_db, mapped, conversations, stats, progress_cb)
    return stats


def _connect(comms_db: Path):
    import sqlite3
    conn = sqlite3.connect(str(comms_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _persist(comms_db, mapped, conversations, stats, progress_cb) -> None:
    conn = _connect(comms_db)
    try:
        _ensure_base_schema(conn)

        # Upsert conversations (COALESCE-guarded, like the iMessage adapter).
        for c in conversations.values():
            existing = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (c["id"],)
            ).fetchone()
            metadata = json.dumps(
                {"channel_type": c["channel_type"]}, ensure_ascii=False
            )
            if existing:
                conn.execute(
                    """UPDATE conversations SET
                         name = COALESCE(NULLIF(?, ''), name),
                         last_message_at = MAX(COALESCE(last_message_at, ''), ?),
                         message_count = ?,
                         metadata = ?
                       WHERE id = ?""",
                    (c["name"] or "", c["last_message_at"],
                     c["message_count"], metadata, c["id"]),
                )
                stats.conversations_updated += 1
            else:
                conn.execute(
                    """INSERT INTO conversations
                       (id, channel, person_id, name, status, last_message_at,
                        message_count, unread_count, metadata)
                       VALUES (?, 'slack', NULL, ?, 'open', ?, ?, 0, ?)""",
                    (c["id"], c["name"], c["last_message_at"],
                     c["message_count"], metadata),
                )
                stats.conversations_created += 1

        # Precise pre-existing count so we report honest insert numbers.
        ids = [m["id"] for m in mapped]
        existing_ids: set[str] = set()
        CHUNK = 500
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i : i + CHUNK]
            ph = ",".join("?" * len(chunk))
            for (rid,) in conn.execute(
                f"SELECT id FROM messages WHERE id IN ({ph})", chunk
            ):
                existing_ids.add(rid)
        stats.skipped_existing = len(existing_ids)

        batch = [
            (
                m["id"], m["channel"], m["direction"], m["sender_id"],
                m["recipient_id"], m["content"], m["timestamp"], m["thread_id"],
                m["has_attachment"], m["attachment_type"], m["attachment_path"],
                m["channel_metadata"], m["person_id"], m["conversation_id"],
            )
            for m in mapped
        ]
        INSERT_SQL = """
            INSERT OR IGNORE INTO messages
              (id, channel, direction, sender_id, recipient_id, content,
               timestamp, thread_id, has_attachment, attachment_type,
               attachment_path, channel_metadata, person_id, conversation_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        for i in range(0, len(batch), CHUNK):
            conn.executemany(INSERT_SQL, batch[i : i + CHUNK])
            if progress_cb:
                progress_cb("persisting", min(i + CHUNK, len(batch)), len(batch))
        conn.commit()
        # ``mapped`` minus rows already present = genuinely new. Do NOT use
        # conn.total_changes — the messages_fts triggers inflate it per row.
        stats.inserted = len(batch) - stats.skipped_existing
    except Exception as e:
        conn.rollback()
        stats.errors.append(str(e))
        logger.exception("Slack ingest failed")
        raise
    finally:
        conn.close()


def _print_scope_help(stats: IngestStats, err: MissingScopeError | None = None) -> None:
    """Print the exact scopes to add and mark the run as a graceful skip."""
    stats.skipped_no_scope = True
    print("Slack: token is missing required read scopes — skipping (not an error).")
    if err is not None and err.needed:
        print(f"  {err.method} needs: {err.needed}")
    print("  Add these scopes to the Slack app, reinstall, and re-run:")
    print("    " + ", ".join(REQUIRED_SCOPES))


# ── CLI entry point ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest Slack conversation history into comms.db"
    )
    parser.add_argument("--since", type=str, default=None,
                        help="Backfill from this date (YYYY-MM-DD), widening the watermark")
    parser.add_argument("--days", type=int, default=14,
                        help="Fresh-DB backfill window in days (default: 14)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Hard cap on messages scanned per channel (testing)")
    parser.add_argument("--channel", type=str, default=None,
                        help="Only ingest this channel (id or name)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and map but do not write")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since)

    def _cb(phase, current, total):
        if total and current and current % 25 == 0:
            print(f"  {phase}: {current:,} / {total:,}")

    stats = ingest(
        since=since, limit=args.limit, dry_run=args.dry_run,
        channel=args.channel, default_days=args.days, progress_cb=_cb,
    )

    print()
    print(stats.summary())
    # Graceful skips (no token / missing scope) are an honest exit 0 — the
    # nightly cron must stay green while scopes are being provisioned.
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
