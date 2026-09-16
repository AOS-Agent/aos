"""iMessage scope gate — the one place "which threads may AOS see" is decided.

macOS Full Disk Access is all-or-nothing: once a process can open
``~/Library/Messages/chat.db`` it can read every conversation on the Mac.
Nothing in the OS narrows that to one thread, so the narrowing has to happen
in AOS — and it has to happen *once*, not in each of the ~30 queries spread
across the adapter, the desktop ingest, Converse and Sentinel. This module is
that one place.

How the gate works
------------------
The policy lives in ``~/.aos/config/comms.yaml``::

    imessage:
      access: allowlist          # allowlist | all   (missing file/key => all)
      include_groups: false
      allowed:
        - name: Hisham
          handles: ["+14165550123", "hisham@example.com"]

``apply(conn)`` is called right after any connection to chat.db is opened.
When the policy is an allowlist it installs SQLite **temp views** named
``message``, ``chat``, ``handle``, ``chat_handle_join``, ``chat_message_join``,
``attachment`` and ``message_attachment_join``. SQLite resolves an unqualified
table name against the ``temp`` schema before ``main``, so from that point on
every existing and future query on the connection — ``SELECT … FROM message``
— sees only the rows the allowlist permits. It works on a ``?mode=ro``
connection because the temp schema is in-memory and always writable, and it
never copies the database.

Missing config, or ``access: all``, installs nothing: today's behaviour,
unchanged, on every machine that never wrote a comms.yaml.

The one bypass
--------------
A query that names the schema explicitly — ``SELECT … FROM main.message`` —
skips the view and sees everything. No code in the comms engine may qualify a
chat.db table with ``main.``; the guard test in ``tests/`` fails on any hit.

Audit trail
-----------
Every ``apply()`` appends one line to ``~/.aos/logs/comms.log``::

    2026-09-15T19:20:01 scope=allowlist source=adapter chats=1 handles=2

so "is it actually restricted?" is answerable from the log, not from trust.

Sending
-------
``send_allowed(recipient)`` / ``assert_send_allowed(recipient)`` gate the
outbound side with the same handle rules, so a mistyped name can never send
to someone outside the allowlist.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".aos" / "config" / "comms.yaml"
DEFAULT_LOG_PATH = Path.home() / ".aos" / "logs" / "comms.log"
CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Tables shadowed by temp views when the policy is an allowlist. Order matters
# only for readability — each view selects from ``main.<table>`` directly.
GATED_TABLES = (
    "handle",
    "chat",
    "chat_handle_join",
    "chat_message_join",
    "message",
    "message_attachment_join",
    "attachment",
)

ACCESS_ALL = "all"
ACCESS_ALLOWLIST = "allowlist"


class ScopeDenied(PermissionError):
    """Raised when an outbound recipient is outside the iMessage allowlist."""


# ── Handle normalisation ─────────────────────────────────────────────────


def _normalize(handle: str) -> tuple[str, str]:
    """Return ``(kind, key)`` for a handle.

    kind is ``"email"`` (key = lowercase address) or ``"phone"``
    (key = digits only). Anything without ``@`` is treated as a phone.
    """
    h = (handle or "").strip()
    if "@" in h:
        return "email", h.lower()
    return "phone", re.sub(r"\D", "", h)


def _phones_match(a: str, b: str) -> bool:
    """Digits-only comparison tolerant of country-code prefixes.

    Two numbers match when their trailing 10 digits are equal (NANP-style
    ``+1 416 555 0123`` vs ``4165550123``). Shorter strings must be identical.
    """
    if not a or not b:
        return False
    if len(a) >= 10 and len(b) >= 10:
        return a[-10:] == b[-10:]
    return a == b


# ── Policy ───────────────────────────────────────────────────────────────


@dataclass
class AllowedContact:
    name: str
    handles: list[str] = field(default_factory=list)


@dataclass
class Policy:
    access: str = ACCESS_ALL
    include_groups: bool = False
    allowed: list[AllowedContact] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def is_restricted(self) -> bool:
        return self.access == ACCESS_ALLOWLIST

    def _match(self, handle: str) -> AllowedContact | None:
        kind, key = _normalize(handle)
        if not key:
            return None
        for contact in self.allowed:
            for allowed in contact.handles:
                a_kind, a_key = _normalize(allowed)
                if a_kind != kind:
                    continue
                if kind == "email" and a_key == key:
                    return contact
                if kind == "phone" and _phones_match(a_key, key):
                    return contact
        return None

    def handle_allowed(self, handle: str) -> bool:
        """True when the policy is unrestricted or the handle is allowlisted."""
        if not self.is_restricted:
            return True
        return self._match(handle) is not None

    def name_for(self, handle: str) -> str | None:
        """The allowlist name a handle belongs to, or None."""
        contact = self._match(handle)
        return contact.name if contact else None

    def handles_for(self, name: str) -> list[str]:
        """All handles registered under an allowlist name (case-insensitive)."""
        want = (name or "").strip().lower()
        for contact in self.allowed:
            if contact.name.strip().lower() == want:
                return list(contact.handles)
        return []


def _config_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("AOS_COMMS_CONFIG")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_PATH


def load_policy(path: Path | str | None = None) -> Policy:
    """Read the iMessage policy. Missing file or key → unrestricted."""
    cfg_path = _config_path(path)
    if not cfg_path.exists():
        return Policy(source_path=cfg_path)
    try:
        with open(cfg_path) as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return Policy(source_path=cfg_path)

    section = raw.get("imessage") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        return Policy(source_path=cfg_path)

    access = str(section.get("access") or ACCESS_ALL).strip().lower()
    if access not in (ACCESS_ALL, ACCESS_ALLOWLIST):
        access = ACCESS_ALL

    allowed: list[AllowedContact] = []
    for entry in section.get("allowed") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        handles = entry.get("handles") or []
        if isinstance(handles, str):
            handles = [handles]
        handles = [str(h).strip() for h in handles if str(h).strip()]
        if name and handles:
            allowed.append(AllowedContact(name=name, handles=handles))

    return Policy(
        access=access,
        include_groups=bool(section.get("include_groups", False)),
        allowed=allowed,
        source_path=cfg_path,
    )


# ── Gate ─────────────────────────────────────────────────────────────────


@dataclass
class ScopeResult:
    access: str
    source: str
    chats: int
    handles: int
    handle_rowids: list[int] = field(default_factory=list)
    chat_rowids: list[int] = field(default_factory=list)

    @property
    def restricted(self) -> bool:
        return self.access == ACCESS_ALLOWLIST


def _log_path() -> Path:
    env = os.environ.get("AOS_COMMS_LOG")
    return Path(env).expanduser() if env else DEFAULT_LOG_PATH


def _log(result: ScopeResult) -> None:
    line = (
        f"{datetime.now().isoformat(timespec='seconds')} "
        f"scope={result.access} source={result.source or '-'} "
        f"chats={result.chats} handles={result.handles}\n"
    )
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(line)
    except OSError:
        pass  # the gate must never fail because the log is unwritable


def _sql_list(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids) or "-1"


def _allowed_handle_rowids(conn: sqlite3.Connection, policy: Policy) -> list[int]:
    rows = conn.execute("SELECT ROWID, id FROM main.handle").fetchall()
    return [int(r[0]) for r in rows if policy.handle_allowed(str(r[1] or ""))]


def _allowed_chat_rowids(
    conn: sqlite3.Connection, policy: Policy, handle_rowids: list[int]
) -> list[int]:
    allowed = set(handle_rowids)
    members: dict[int, set[int]] = {}
    for chat_id, handle_id in conn.execute(
        "SELECT chat_id, handle_id FROM main.chat_handle_join"
    ):
        members.setdefault(int(chat_id), set()).add(int(handle_id))

    out: list[int] = []
    for rowid, identifier in conn.execute(
        "SELECT ROWID, chat_identifier FROM main.chat"
    ):
        rowid = int(rowid)
        participants = members.get(rowid)
        if participants:
            hits = participants & allowed
            if policy.include_groups:
                ok = bool(hits)
            else:
                ok = bool(hits) and participants <= allowed
        else:
            # No join rows (some SMS/short-code chats): fall back to the
            # chat_identifier, which is the handle string for 1:1 chats.
            ok = policy.handle_allowed(str(identifier or ""))
        if ok:
            out.append(rowid)
    return out


def _install_views(
    conn: sqlite3.Connection, handle_rowids: list[int], chat_rowids: list[int]
) -> None:
    h = _sql_list(handle_rowids)
    c = _sql_list(chat_rowids)
    msgs = f"SELECT message_id FROM main.chat_message_join WHERE chat_id IN ({c})"
    views = {
        "handle": f"SELECT * FROM main.handle WHERE ROWID IN ({h})",
        "chat": f"SELECT * FROM main.chat WHERE ROWID IN ({c})",
        "chat_handle_join": (
            f"SELECT * FROM main.chat_handle_join "
            f"WHERE chat_id IN ({c}) AND handle_id IN ({h})"
        ),
        "chat_message_join": (
            f"SELECT * FROM main.chat_message_join WHERE chat_id IN ({c})"
        ),
        "message": f"SELECT * FROM main.message WHERE ROWID IN ({msgs})",
        "message_attachment_join": (
            f"SELECT * FROM main.message_attachment_join WHERE message_id IN ({msgs})"
        ),
        "attachment": (
            f"SELECT * FROM main.attachment WHERE ROWID IN ("
            f"SELECT attachment_id FROM main.message_attachment_join "
            f"WHERE message_id IN ({msgs}))"
        ),
    }
    existing = {
        r[0]
        for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
    }
    for name in GATED_TABLES:
        conn.execute(f"DROP VIEW IF EXISTS temp.{name}")
        if name not in existing:
            continue  # a fixture or stripped chat.db without this table
        conn.execute(f"CREATE TEMP VIEW {name} AS {views[name]}")


def apply(
    conn: sqlite3.Connection, policy: Policy | None = None, *, source: str = ""
) -> ScopeResult:
    """Install the allowlist views on ``conn`` (a chat.db connection).

    Unrestricted policy → installs nothing, still logs one ``scope=all`` line.
    Restricted policy → temp views shadow every gated table; only allowlisted
    handles, the 1:1 chats made of them (groups only with ``include_groups``),
    and those chats' messages/attachments remain visible to unqualified queries.
    """
    policy = policy or load_policy()
    if not policy.is_restricted:
        result = ScopeResult(access=ACCESS_ALL, source=source, chats=-1, handles=-1)
        _log(result)
        return result

    handle_rowids = _allowed_handle_rowids(conn, policy)
    chat_rowids = _allowed_chat_rowids(conn, policy, handle_rowids)
    _install_views(conn, handle_rowids, chat_rowids)
    result = ScopeResult(
        access=ACCESS_ALLOWLIST,
        source=source,
        chats=len(chat_rowids),
        handles=len(handle_rowids),
        handle_rowids=handle_rowids,
        chat_rowids=chat_rowids,
    )
    _log(result)
    return result


def open_chat_db(
    path: Path | str | None = None,
    *,
    source: str,
    policy: Policy | None = None,
) -> sqlite3.Connection:
    """Open chat.db read-only and apply the scope gate before returning it."""
    db = Path(path).expanduser() if path else CHAT_DB_PATH
    if not db.exists():
        raise FileNotFoundError(str(db))
    uri = f"file:{db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    apply(conn, policy, source=source)
    return conn


# ── Outbound ─────────────────────────────────────────────────────────────


def send_allowed(recipient: str, policy: Policy | None = None) -> bool:
    """True when a send to ``recipient`` (phone/email) is inside the policy."""
    policy = policy or load_policy()
    return policy.handle_allowed(recipient)


def assert_send_allowed(recipient: str, policy: Policy | None = None) -> None:
    if not send_allowed(recipient, policy):
        raise ScopeDenied(
            f"iMessage send to {recipient!r} refused: not in the allowlist "
            f"({_config_path()})"
        )


# ── CLI ──────────────────────────────────────────────────────────────────


def _status() -> int:
    policy = load_policy()
    print(f"scope={policy.access}  config={policy.source_path}")
    if policy.is_restricted:
        if not policy.allowed:
            print("  allowed: (none — every thread is hidden)")
        for contact in policy.allowed:
            print(f"  allowed: {contact.name}  {', '.join(contact.handles)}")
        print(f"  include_groups: {'yes' if policy.include_groups else 'no'}")
    else:
        print("  every iMessage thread is visible (no allowlist configured)")

    try:
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True, timeout=2)
        total_chats = conn.execute("SELECT COUNT(*) FROM main.chat").fetchone()[0]
        total_handles = conn.execute("SELECT COUNT(*) FROM main.handle").fetchone()[0]
    except sqlite3.Error:
        print("chat.db: not readable (Full Disk Access?)")
        return 0

    result = apply(conn, policy, source="status")
    if result.restricted:
        print(
            f"chat.db: {result.chats} of {total_chats} chats visible, "
            f"{result.handles} of {total_handles} handles"
        )
        for row in conn.execute(
            "SELECT chat_identifier, display_name FROM chat ORDER BY ROWID"
        ):
            label = row[1] or ""
            name = policy.name_for(row[0]) or ""
            tag = f"  ({name})" if name else (f"  [{label}]" if label else "")
            print(f"    - {row[0]}{tag}")
    else:
        print(f"chat.db: {total_chats} chats, {total_handles} handles visible")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if cmd in ("status", "show"):
        return _status()
    print("usage: scope.py status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
