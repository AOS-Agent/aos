"""Static guard: every chat.db reader and iMessage sender goes through scope.py.

macOS Full Disk Access is all-or-nothing, so the iMessage allowlist in
``core/engine/comms/scope.py`` is the only thing keeping AOS to the threads
the operator permitted. That holds only while every connection to
``~/Library/Messages/chat.db`` is gated. These checks read the source tree —
no database, no network — and fail the moment someone adds:

1. a file that opens chat.db without calling ``scope.apply`` / ``open_chat_db``
2. a hand-built read-only chat.db URI outside scope.py
3. a ``main.``-qualified chat.db table name (the one bypass of the temp views)
4. an AppleScript ``send`` to Messages without a ``send_allowed`` check

Each assertion names the offending files.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMS = REPO / "core" / "engine" / "comms"
SCOPE = COMMS / "scope.py"

# Standalone scripts outside core/engine/comms that also touch chat.db or
# send through Messages.app.
EXTRA_SCRIPTS = [
    REPO / "core" / "bin" / "internal" / "operator-link",
    REPO / "core" / "bin" / "cli" / "converse",
    REPO / "core" / "bin" / "cli" / "envoy",
]

GATED_TABLES = (
    "message",
    "chat",
    "handle",
    "chat_message_join",
    "chat_handle_join",
    "attachment",
    "message_attachment_join",
)

# A chat.db path expression as it appears in a read-only URI.
_CHAT_DB_EXPR = r"(?:CHAT_DB(?:_PATH)?|IMESSAGE_DB|chat_db|imessage_db|db)"
_RO_URI = re.compile(r"file:\{" + _CHAT_DB_EXPR + r"\}\?mode=ro")
# A direct sqlite open whose argument is not obviously one of the other AOS
# stores. Combined with a chat.db mention it marks a reader — the adapter
# opens a temp *copy* of chat.db and the ingest opens a snapshot, so the
# argument is not always the chat.db path itself.
_CONNECT = re.compile(r"sqlite3\.connect\(\s*(?:str\()?\s*([^,)]*)")
_OTHER_STORES = re.compile(
    r"COMMS_DB|PEOPLE_DB|comms_db|people_db|WORK_DB|work_db|CONVERSE_DB|SESSIONS_DB",
    re.IGNORECASE,
)
_MAIN_QUALIFIED = re.compile(r"\bmain\.(?:" + "|".join(GATED_TABLES) + r")\b")
_SEND_CMD = re.compile(r'\bsend\s+"')


def _comms_sources() -> list[Path]:
    files = [
        p
        for p in COMMS.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    ]
    files += [p for p in EXTRA_SCRIPTS if p.exists()]
    return sorted(files)


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def _strip_comments(text: str) -> str:
    """Drop ``#`` comment lines so a mention in prose never counts as an open."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _mentions_chat_db(text: str) -> bool:
    return "chat.db" in text or "CHAT_DB" in text or "IMESSAGE_DB" in text


def _touches_chat_db(text: str) -> bool:
    """True when the file opens a database *and* names chat.db.

    A file that names chat.db only in a comment or an ``exists()`` probe and
    never opens any sqlite connection is not a reader. A file that does both
    (even if the connect is to a temp copy or a snapshot of chat.db) is.
    """
    code = _strip_comments(text)
    if not _mentions_chat_db(code):
        return False
    if _RO_URI.search(code) or "open_chat_db(" in code:
        return True
    return any(not _OTHER_STORES.search(m.group(1)) for m in _CONNECT.finditer(code))


def _uses_gate(text: str) -> bool:
    return "scope" in text and ("scope.apply(" in text or "open_chat_db(" in text)


def test_every_chat_db_reader_uses_the_gate():
    offenders = []
    for path in _comms_sources():
        if path == SCOPE:
            continue
        text = path.read_text()
        if _touches_chat_db(text) and not _uses_gate(text):
            offenders.append(_rel(path))
    assert not offenders, (
        "chat.db opened without the scope gate (call scope.apply(conn, source=...) "
        "or scope.open_chat_db(...)): " + ", ".join(offenders)
    )


def test_no_read_only_chat_db_uri_outside_scope():
    offenders = []
    for path in _comms_sources():
        if path == SCOPE:
            continue
        text = path.read_text()
        for m in _RO_URI.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{_rel(path)}:{line}")
    assert not offenders, (
        "hand-built chat.db ?mode=ro URI — use scope.open_chat_db(...) instead: "
        + ", ".join(offenders)
    )


def test_no_main_qualified_chat_db_tables_outside_scope():
    offenders = []
    for path in _comms_sources():
        if path == SCOPE:
            continue
        text = path.read_text()
        for m in _MAIN_QUALIFIED.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{_rel(path)}:{line} ({m.group(0)})")
    assert not offenders, (
        "main.<table> bypasses the allowlist views; query the unqualified name: "
        + ", ".join(offenders)
    )


def test_every_messages_send_checks_the_allowlist():
    offenders = []
    for path in _comms_sources():
        if path == SCOPE:
            continue
        text = path.read_text()
        if 'tell application "Messages"' not in text:
            continue
        if not _SEND_CMD.search(text):
            continue
        if "send_allowed(" not in text and "assert_send_allowed(" not in text:
            offenders.append(_rel(path))
    assert not offenders, (
        "AppleScript send to Messages without scope.send_allowed(recipient): "
        + ", ".join(offenders)
    )


def test_guard_sees_the_known_readers_and_senders():
    """The guard is only worth something if it actually matches the real files."""
    readers = {_rel(p) for p in _comms_sources() if _touches_chat_db(p.read_text())}
    expected_readers = {
        "core/engine/comms/channels/imessage.py",
        "core/engine/comms/channels/imessage_desktop.py",
        "core/engine/comms/converse/channels_imessage.py",
        "core/engine/comms/sentinel/watcher.py",
        "core/engine/comms/sentinel/ack.py",
        "core/engine/comms/sentinel/context_builder.py",
        "core/bin/internal/operator-link",
    }
    missing = expected_readers - readers
    assert not missing, f"guard no longer detects known chat.db readers: {missing}"

    senders = {
        _rel(p)
        for p in _comms_sources()
        if 'tell application "Messages"' in p.read_text()
    }
    expected_senders = {
        "core/engine/comms/channels/imessage.py",
        "core/engine/comms/converse/channels_imessage.py",
        "core/engine/comms/envoy/runner.py",
        "core/engine/comms/sentinel/ack.py",
    }
    missing = expected_senders - senders
    assert not missing, f"guard no longer detects known Messages senders: {missing}"
