"""Tests for core/engine/comms/scope.py — the iMessage allowlist gate.

Every test builds a throwaway chat.db in tmp_path with FAKE data (NANP
555 numbers, example.com addresses). No test reads the operator's real
~/Library/Messages/chat.db, ~/.aos/config/comms.yaml or ~/.aos/logs/comms.log:
both paths are redirected through the AOS_COMMS_CONFIG / AOS_COMMS_LOG
environment overrides for the duration of each test.

Runs under pytest, and standalone via `python3 tests/test_comms_scope.py`.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# scope.py lives in core/engine/comms/
_COMMS_DIR = Path(__file__).parent.parent / "core" / "engine" / "comms"
if str(_COMMS_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMS_DIR))

import scope  # noqa: E402
from scope import (  # noqa: E402
    Policy,
    ScopeDenied,
    apply,
    assert_send_allowed,
    load_policy,
    open_chat_db,
    send_allowed,
)

# ── Fixture builders ──────────────────────────────────────────────────────

# The subset of the live chat.db schema the engine actually queries. Column
# names are the real ones so a query written against production parses here.
_CHAT_SCHEMA = """
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY,
    id TEXT,
    service TEXT
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY,
    chat_identifier TEXT,
    style INTEGER,
    display_name TEXT
);
CREATE TABLE chat_handle_join (
    chat_id INTEGER,
    handle_id INTEGER
);
CREATE TABLE chat_message_join (
    chat_id INTEGER,
    message_id INTEGER
);
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY,
    handle_id INTEGER,
    text TEXT,
    is_from_me INTEGER,
    date INTEGER
);
CREATE TABLE attachment (
    ROWID INTEGER PRIMARY KEY,
    filename TEXT
);
CREATE TABLE message_attachment_join (
    message_id INTEGER,
    attachment_id INTEGER
);
"""

HISHAM_PHONE = "+14165550100"
HISHAM_EMAIL = "hisham@example.com"
OTHER_PHONE = "+14165550199"
THIRD_PHONE = "+14165550177"


def _build_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_CHAT_SCHEMA)
    # handles: 1 = Hisham phone, 2 = Hisham email, 3 = other, 4 = third
    conn.executemany(
        "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, ?)",
        [
            (1, HISHAM_PHONE, "iMessage"),
            (2, HISHAM_EMAIL.upper(), "iMessage"),  # stored upper-case on purpose
            (3, OTHER_PHONE, "iMessage"),
            (4, THIRD_PHONE, "SMS"),
        ],
    )
    # chats: 10 = Hisham 1:1 (phone), 11 = Hisham 1:1 (email), 12 = other 1:1,
    #        13 = group with Hisham + other, 14 = short-code chat with no join rows
    conn.executemany(
        "INSERT INTO chat (ROWID, chat_identifier, style, display_name) VALUES (?, ?, ?, ?)",
        [
            (10, HISHAM_PHONE, 45, None),
            (11, HISHAM_EMAIL, 45, None),
            (12, OTHER_PHONE, 45, None),
            (13, "chat123456", 43, "Qren crew"),
            (14, "22000", 45, None),
        ],
    )
    conn.executemany(
        "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
        [(10, 1), (11, 2), (12, 3), (13, 1), (13, 3), (13, 4)],
    )
    # messages: 100-102 Hisham phone chat (one from me), 103 Hisham email,
    #           104-105 other chat, 106-107 group, 108 short-code
    conn.executemany(
        "INSERT INTO message (ROWID, handle_id, text, is_from_me, date) VALUES (?, ?, ?, ?, ?)",
        [
            (100, 1, "hey", 0, 1),
            (101, 0, "hey back", 1, 2),  # from me: handle_id 0
            (102, 1, "pr is up", 0, 3),
            (103, 2, "email hello", 0, 4),
            (104, 3, "other person", 0, 5),
            (105, 0, "reply to other", 1, 6),
            (106, 3, "group msg from other", 0, 7),
            (107, 1, "group msg from hisham", 0, 8),
            (108, 0, "your code is 1234", 0, 9),
        ],
    )
    conn.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [
            (10, 100),
            (10, 101),
            (10, 102),
            (11, 103),
            (12, 104),
            (12, 105),
            (13, 106),
            (13, 107),
            (14, 108),
        ],
    )
    conn.executemany(
        "INSERT INTO attachment (ROWID, filename) VALUES (?, ?)",
        [(200, "hisham.png"), (201, "other.png")],
    )
    conn.executemany(
        "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
        [(102, 200), (104, 201)],
    )
    conn.commit()
    conn.close()


def _write_config(path: Path, text: str) -> None:
    path.write_text(text)


ALLOWLIST_YAML = f"""
imessage:
  access: allowlist
  allowed:
    - name: Hisham
      handles:
        - "{HISHAM_PHONE}"
        - "{HISHAM_EMAIL}"
"""


@pytest.fixture
def chat_db(tmp_path: Path) -> Path:
    db = tmp_path / "chat.db"
    _build_chat_db(db)
    return db


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Redirect config + log to tmp_path. Returns (config_path, log_path)."""
    cfg = tmp_path / "comms.yaml"
    log = tmp_path / "logs" / "comms.log"
    monkeypatch.setenv("AOS_COMMS_CONFIG", str(cfg))
    monkeypatch.setenv("AOS_COMMS_LOG", str(log))
    return cfg, log


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _ids(conn, sql):
    return sorted(int(r[0]) for r in conn.execute(sql))


# ── Policy loading ────────────────────────────────────────────────────────


def test_missing_config_is_unrestricted(env):
    cfg, _ = env
    assert not cfg.exists()
    p = load_policy()
    assert p.access == "all"
    assert not p.is_restricted
    assert p.handle_allowed(OTHER_PHONE)


def test_access_all_is_unrestricted(env):
    cfg, _ = env
    _write_config(cfg, "imessage:\n  access: all\n")
    assert not load_policy().is_restricted


def test_allowlist_loads_contacts(env):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    p = load_policy()
    assert p.is_restricted
    assert [c.name for c in p.allowed] == ["Hisham"]
    assert p.handles_for("hisham") == [HISHAM_PHONE, HISHAM_EMAIL]
    assert p.name_for(HISHAM_PHONE) == "Hisham"
    assert p.name_for(OTHER_PHONE) is None


def test_unknown_access_value_falls_back_to_all(env):
    cfg, _ = env
    _write_config(cfg, "imessage:\n  access: whatever\n")
    assert load_policy().access == "all"


def test_malformed_yaml_is_unrestricted(env):
    cfg, _ = env
    _write_config(cfg, "imessage: [unclosed\n")
    assert not load_policy().is_restricted


# ── Handle matching ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "handle",
    [
        HISHAM_PHONE,
        "4165550100",
        "+1 (416) 555-0100",
        "1-416-555-0100",
        "416 555 0100",
    ],
)
def test_phone_normalization(handle):
    p = Policy(access="allowlist", allowed=[scope.AllowedContact("H", [HISHAM_PHONE])])
    assert p.handle_allowed(handle)


def test_phone_different_number_denied():
    p = Policy(access="allowlist", allowed=[scope.AllowedContact("H", [HISHAM_PHONE])])
    assert not p.handle_allowed(OTHER_PHONE)
    assert not p.handle_allowed("4165550199")


def test_short_codes_must_match_exactly():
    p = Policy(access="allowlist", allowed=[scope.AllowedContact("Bank", ["22000"])])
    assert p.handle_allowed("22000")
    assert not p.handle_allowed("22001")
    assert not p.handle_allowed("1422000")


def test_email_case_insensitive():
    p = Policy(access="allowlist", allowed=[scope.AllowedContact("H", [HISHAM_EMAIL])])
    assert p.handle_allowed("Hisham@Example.COM")
    assert not p.handle_allowed("hisham@example.org")


def test_email_and_phone_kinds_never_cross():
    p = Policy(access="allowlist", allowed=[scope.AllowedContact("H", ["4165550100"])])
    assert not p.handle_allowed("4165550100@example.com")


# ── The gate ──────────────────────────────────────────────────────────────


def test_no_config_leaves_everything_visible(env, chat_db):
    _, log = env
    conn = _ro(chat_db)
    result = apply(conn, source="test")
    assert not result.restricted
    assert _ids(conn, "SELECT ROWID FROM message") == list(range(100, 109))
    assert _ids(conn, "SELECT ROWID FROM chat") == [10, 11, 12, 13, 14]
    assert _ids(conn, "SELECT ROWID FROM handle") == [1, 2, 3, 4]
    text = log.read_text()
    assert "scope=all" in text
    assert "scope=allowlist" not in text


def test_allowlist_restricts_unqualified_queries_on_ro_connection(env, chat_db):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    result = apply(conn, source="test")

    assert result.restricted
    assert result.chats == 2
    assert result.handles == 2
    assert sorted(result.chat_rowids) == [10, 11]
    assert sorted(result.handle_rowids) == [1, 2]

    assert _ids(conn, "SELECT ROWID FROM handle") == [1, 2]
    assert _ids(conn, "SELECT ROWID FROM chat") == [10, 11]
    # from-me rows (handle_id 0) in Hisham's chat survive; other chat's do not
    assert _ids(conn, "SELECT ROWID FROM message") == [100, 101, 102, 103]
    assert _ids(conn, "SELECT chat_id FROM chat_handle_join") == [10, 11]
    assert _ids(conn, "SELECT message_id FROM chat_message_join") == [
        100,
        101,
        102,
        103,
    ]
    assert _ids(conn, "SELECT ROWID FROM attachment") == [200]
    assert _ids(conn, "SELECT attachment_id FROM message_attachment_join") == [200]

    # a production-shaped join still works through the views
    rows = conn.execute(
        """
        SELECT m.text, h.id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        ORDER BY m.ROWID
        """
    ).fetchall()
    assert [r[0] for r in rows] == ["hey", "hey back", "pr is up", "email hello"]


def test_main_qualified_query_bypasses_the_views(env, chat_db):
    """Documents the one bypass: `main.message` skips the temp view.

    The guard test in Part 3 forbids that qualifier in comms code.
    """
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    apply(conn, source="test")
    assert _ids(conn, "SELECT ROWID FROM message") == [100, 101, 102, 103]
    assert _ids(conn, "SELECT ROWID FROM main.message") == list(range(100, 109))


def test_group_chats_hidden_by_default_included_on_request(env, chat_db):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    apply(conn, source="test")
    assert 13 not in _ids(conn, "SELECT ROWID FROM chat")
    assert 107 not in _ids(conn, "SELECT ROWID FROM message")
    conn.close()

    _write_config(cfg, ALLOWLIST_YAML + "  include_groups: true\n")
    conn = _ro(chat_db)
    result = apply(conn, source="test")
    assert sorted(result.chat_rowids) == [10, 11, 13]
    # the group's other members' messages come with it — by design
    assert _ids(conn, "SELECT ROWID FROM message") == [100, 101, 102, 103, 106, 107]
    # but their handles are still not exposed via the handle view
    assert _ids(conn, "SELECT ROWID FROM handle") == [1, 2]


def test_chat_without_join_rows_matches_on_identifier(env, chat_db):
    cfg, _ = env
    _write_config(
        cfg,
        'imessage:\n  access: allowlist\n  allowed:\n    - name: Bank\n      handles: ["22000"]\n',
    )
    conn = _ro(chat_db)
    result = apply(conn, source="test")
    assert result.chat_rowids == [14]
    assert result.handles == 0
    assert _ids(conn, "SELECT ROWID FROM message") == [108]


def test_empty_allowlist_hides_everything(env, chat_db):
    cfg, _ = env
    _write_config(cfg, "imessage:\n  access: allowlist\n  allowed: []\n")
    conn = _ro(chat_db)
    result = apply(conn, source="test")
    assert result.restricted
    assert result.chats == 0
    assert _ids(conn, "SELECT ROWID FROM message") == []
    assert _ids(conn, "SELECT ROWID FROM chat") == []


def test_apply_is_idempotent_on_same_connection(env, chat_db):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    apply(conn, source="one")
    apply(conn, source="two")  # DROP VIEW IF EXISTS keeps this from erroring
    assert _ids(conn, "SELECT ROWID FROM chat") == [10, 11]


def test_explicit_policy_overrides_config(env, chat_db):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    result = apply(conn, Policy(access="all"), source="test")
    assert not result.restricted
    assert _ids(conn, "SELECT ROWID FROM chat") == [10, 11, 12, 13, 14]


# ── Audit log ─────────────────────────────────────────────────────────────


def test_log_line_has_source_and_counts(env, chat_db):
    cfg, log = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = _ro(chat_db)
    apply(conn, source="adapter")
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("scope=allowlist source=adapter chats=2 handles=2")
    # ISO timestamp first
    assert lines[0][:4].isdigit() and "T" in lines[0].split(" ")[0]


def test_log_appends(env, chat_db):
    cfg, log = env
    _write_config(cfg, ALLOWLIST_YAML)
    apply(_ro(chat_db), source="a")
    apply(_ro(chat_db), source="b")
    assert len(log.read_text().strip().splitlines()) == 2


# ── open_chat_db ──────────────────────────────────────────────────────────


def test_open_chat_db_applies_gate(env, chat_db):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    conn = open_chat_db(chat_db, source="test")
    assert _ids(conn, "SELECT ROWID FROM chat") == [10, 11]
    # read-only: a write must fail
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO main.handle (id) VALUES ('x')")


def test_open_chat_db_missing_file_raises(env, tmp_path):
    with pytest.raises(FileNotFoundError):
        open_chat_db(tmp_path / "nope.db", source="test")


# ── Outbound ──────────────────────────────────────────────────────────────


def test_send_allowed_unrestricted(env):
    assert send_allowed(OTHER_PHONE)
    assert_send_allowed(OTHER_PHONE)  # no raise


def test_send_allowed_restricted(env):
    cfg, _ = env
    _write_config(cfg, ALLOWLIST_YAML)
    assert send_allowed(HISHAM_PHONE)
    assert send_allowed("416-555-0100")
    assert send_allowed("HISHAM@example.com")
    assert not send_allowed(OTHER_PHONE)
    assert_send_allowed(HISHAM_PHONE)
    with pytest.raises(ScopeDenied) as exc:
        assert_send_allowed(OTHER_PHONE)
    assert OTHER_PHONE in str(exc.value)
    assert isinstance(exc.value, PermissionError)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
