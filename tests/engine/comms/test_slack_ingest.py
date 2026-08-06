"""Tests for the Slack ingest adapter.

No network and no real Slack data: every message is a synthetic fixture and the
Slack Web API is stood in for by ``_FakeTransport`` — a callable
``(method, params) -> dict`` returning canned Slack JSON bodies (with their own
``ok``/``error`` fields), injected via ``SlackClient(transport=...)``.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from core.engine.comms.channels import slack as sl

# ── fixtures ─────────────────────────────────────────────────────────────

SELF = "U_SELF"
TEAM = "T_TEAM"

# End-to-end fixtures use timestamps inside the default 14-day fresh-DB window
# (a real Slack ``ts`` is current epoch seconds; the historical 1970-era values
# used by the pure-mapping tests would be filtered out by the ``oldest`` floor).
_BASE = time.time() - 3600.0


def _rts(offset: float) -> str:
    """A recent Slack-style ts string, ``offset`` seconds past ``_BASE``."""
    return f"{_BASE + offset:.4f}"


def _msg(ts, user, text, *, thread_ts=None, subtype=None, files=None):
    m = {"type": "message", "ts": ts, "user": user, "text": text}
    if thread_ts is not None:
        m["thread_ts"] = thread_ts
    if subtype is not None:
        m["subtype"] = subtype
    if files is not None:
        m["files"] = files
    return m


def _conv(cid, *, name=None, is_im=False, user=None, is_private=False):
    c = {"id": cid}
    if name is not None:
        c["name"] = name
    if is_im:
        c["is_im"] = True
        c["user"] = user
    if is_private:
        c["is_private"] = True
    return c


class _FakeTransport:
    """Stand-in Slack Web API. ``history`` maps channel_id -> [messages]."""

    def __init__(self, *, users=None, conversations=None, history=None,
                 fail=None):
        self._users = users or []
        self._conversations = conversations or []
        self._history = history or {}
        # fail: {method_name: error_string} → returns ok:false for that method.
        self._fail = fail or {}
        self.calls: list[str] = []

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append(method)
        if method in self._fail:
            err = self._fail[method]
            body = {"ok": False, "error": err}
            if err == "missing_scope":
                body["needed"] = "channels:history"
                body["provided"] = "chat:write"
            return body
        if method == "auth.test":
            return {"ok": True, "user_id": SELF, "team": "RunRec HQ",
                    "team_id": TEAM}
        if method == "users.list":
            return {"ok": True, "members": self._users,
                    "response_metadata": {"next_cursor": ""}}
        if method == "conversations.list":
            return {"ok": True, "channels": self._conversations,
                    "response_metadata": {"next_cursor": ""}}
        if method == "conversations.history":
            msgs = self._history.get(params.get("channel"), [])
            oldest = params.get("oldest")
            if oldest is not None:
                lo = float(oldest)
                msgs = [m for m in msgs if float(m["ts"]) >= lo]
            return {"ok": True, "messages": msgs,
                    "response_metadata": {"next_cursor": ""}}
        return {"ok": False, "error": "unknown_method"}


def _client(transport: _FakeTransport) -> sl.SlackClient:
    # min_interval=0 keeps the (never-used) default pacing out of tests.
    return sl.SlackClient("xoxp-test", transport=transport, min_interval=0)


# ── schema bootstrap ─────────────────────────────────────────────────────


def test_ensure_base_schema_creates_tables_on_empty_db(tmp_path):
    db = tmp_path / "comms.db"
    conn = sqlite3.connect(db)
    sl._ensure_base_schema(conn)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
        )
    }
    conn.close()
    assert {"messages", "conversations", "messages_fts",
            "messages_ai", "messages_ad", "messages_au"} <= names


def test_ensure_base_schema_is_idempotent(tmp_path):
    db = tmp_path / "comms.db"
    conn = sqlite3.connect(db)
    sl._ensure_base_schema(conn)
    sl._ensure_base_schema(conn)  # second call must not raise
    # FTS wiring is live: an insert propagates to messages_fts.
    conn.execute(
        "INSERT INTO messages (id, channel, content) VALUES ('x','slack','hello world')"
    )
    conn.commit()
    hit = conn.execute(
        "SELECT id FROM messages WHERE rowid IN "
        "(SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'hello')"
    ).fetchone()
    conn.close()
    assert hit == ("x",)


# ── pure mapping ─────────────────────────────────────────────────────────


def test_ts_to_iso_roundtrips_and_rejects_garbage():
    iso = sl._ts_to_iso("1721577600.001200")
    assert isinstance(iso, str)
    assert datetime.fromisoformat(iso).timestamp() == 1721577600.0012
    assert sl._ts_to_iso("garbage") is None


def test_direction_attribution():
    conv = _conv("C1", name="general")
    out = sl.map_message(_msg("100.0001", SELF, "mine"), conv, SELF, TEAM, {})
    inc = sl.map_message(_msg("100.0002", "U_OTHER", "theirs"), conv, SELF, TEAM, {})
    assert out["direction"] == "outgoing"
    assert inc["direction"] == "incoming"
    assert out["id"] == "slack_C1_100.0001"
    assert out["channel"] == "slack"
    assert out["conversation_id"] == "slack_C1"


def test_thread_id_mapping():
    conv = _conv("C1", name="general")
    # Parent (thread_ts == ts) carries no thread_id; a reply does.
    parent = sl.map_message(_msg("200.0", SELF, "root", thread_ts="200.0"),
                            conv, SELF, TEAM, {})
    reply = sl.map_message(_msg("200.5", "U_OTHER", "re", thread_ts="200.0"),
                           conv, SELF, TEAM, {})
    assert parent["thread_id"] is None
    assert reply["thread_id"] == "200.0"


def test_skips_bookkeeping_and_empty_messages():
    conv = _conv("C1", name="general")
    assert sl.map_message(_msg("1.0", SELF, "hi", subtype="channel_join"),
                          conv, SELF, TEAM, {}) is None
    assert sl.map_message(_msg("2.0", SELF, "   "), conv, SELF, TEAM, {}) is None
    # But an empty-text message with a file attachment is kept.
    row = sl.map_message(
        _msg("3.0", SELF, "", files=[{"mimetype": "image/png"}]),
        conv, SELF, TEAM, {},
    )
    assert row is not None and row["has_attachment"] == 1
    assert row["attachment_type"] == "image/png"


def test_conversation_name_dm_uses_counterpart_username():
    users = {"U_OTHER": "alice"}
    im = _conv("D1", is_im=True, user="U_OTHER")
    assert sl.conversation_name(im, users) == "alice"
    ch = _conv("C1", name="general")
    assert sl.conversation_name(ch, users) == "general"


# ── watermark ────────────────────────────────────────────────────────────


def test_watermark_epoch_from_db(tmp_path):
    db = tmp_path / "comms.db"
    conn = sqlite3.connect(db)
    sl._ensure_base_schema(conn)
    assert sl._watermark_epoch(conn) is None  # empty → no floor
    iso = datetime(2026, 7, 1, 12, 0, 0).isoformat()
    conn.execute(
        "INSERT INTO messages (id, channel, timestamp) VALUES ('slack_C_1', 'slack', ?)",
        (iso,),
    )
    conn.execute(  # a non-slack row must be ignored by the watermark
        "INSERT INTO messages (id, channel, timestamp) VALUES ('gmail:x', 'email', ?)",
        (datetime(2027, 1, 1).isoformat(),),
    )
    conn.commit()
    wm = sl._watermark_epoch(conn)
    conn.close()
    assert wm == datetime(2026, 7, 1, 12, 0, 0).timestamp()


def test_resolve_oldest_precedence(tmp_path):
    db = tmp_path / "comms.db"
    conn = sqlite3.connect(db)
    sl._ensure_base_schema(conn)
    # Fresh DB, no since → now - default_days window.
    fresh = sl._resolve_oldest(conn, None, default_days=14)
    approx = (datetime.now().timestamp() - 14 * 86400)
    assert abs(fresh - approx) < 5

    wm_iso = datetime(2026, 7, 1, 12, 0, 0).isoformat()
    conn.execute(
        "INSERT INTO messages (id, channel, timestamp) VALUES ('slack_C_1','slack',?)",
        (wm_iso,),
    )
    conn.commit()
    wm = datetime(2026, 7, 1, 12, 0, 0).timestamp()
    # Watermark, no since → watermark minus the safety overlap (cheap incremental).
    incr = sl._resolve_oldest(conn, None, default_days=14)
    assert incr == wm - sl.WATERMARK_OVERLAP_S
    # --since earlier than the watermark widens the window back to since.
    since = datetime(2026, 1, 1)
    back = sl._resolve_oldest(conn, since, default_days=14)
    conn.close()
    assert back == since.timestamp()


# ── missing_scope graceful skip ──────────────────────────────────────────


def test_missing_scope_skips_gracefully(tmp_path, capsys):
    transport = _FakeTransport(
        users=[{"id": SELF, "profile": {"display_name": "me"}}],
        conversations=[_conv("C1", name="general")],
        fail={"conversations.list": "missing_scope"},
    )
    stats = sl.ingest(client=_client(transport), comms_db=tmp_path / "comms.db")
    assert stats.skipped_no_scope is True
    assert stats.inserted == 0
    out = capsys.readouterr().out
    # The actionable message lists every required scope.
    for scope in sl.REQUIRED_SCOPES:
        assert scope in out


# ── end-to-end ingest ────────────────────────────────────────────────────


def _basic_transport():
    return _FakeTransport(
        users=[
            {"id": SELF, "profile": {"display_name": "me"}},
            {"id": "U_OTHER", "profile": {"display_name": "alice"}},
        ],
        conversations=[
            _conv("C1", name="general"),
            _conv("D1", is_im=True, user="U_OTHER"),
        ],
        history={
            "C1": [
                _msg(_rts(1), SELF, "hello team"),
                _msg(_rts(2), "U_OTHER", "reply", thread_ts=_rts(1)),
                _msg(_rts(3), SELF, "x", subtype="channel_join"),  # skipped
            ],
            "D1": [
                _msg(_rts(4), "U_OTHER", "dm in"),
            ],
        },
    )


def test_ingest_inserts_messages_and_conversations(tmp_path):
    db = tmp_path / "comms.db"
    stats = sl.ingest(client=_client(_basic_transport()), comms_db=db)
    assert stats.inserted == 3          # 2 in C1 (join skipped) + 1 DM
    assert stats.skipped_no_text == 1   # the channel_join
    assert stats.conversations_created == 2

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, direction FROM messages ORDER BY id"))
    convs = dict(conn.execute("SELECT id, name FROM conversations"))
    conn.close()
    assert rows[f"slack_C1_{_rts(1)}"] == "outgoing"
    assert rows[f"slack_C1_{_rts(2)}"] == "incoming"
    assert rows[f"slack_D1_{_rts(4)}"] == "incoming"
    # DM conversation is named after the counterpart's username.
    assert convs["slack_D1"] == "alice"
    assert convs["slack_C1"] == "general"


def test_ingest_is_idempotent(tmp_path):
    db = tmp_path / "comms.db"
    first = sl.ingest(client=_client(_basic_transport()), comms_db=db)
    second = sl.ingest(client=_client(_basic_transport()), comms_db=db)
    assert first.inserted == 3
    assert second.inserted == 0
    assert second.skipped_existing == 3
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert count == 3


def test_ingest_dry_run_writes_nothing(tmp_path):
    db = tmp_path / "comms.db"
    stats = sl.ingest(client=_client(_basic_transport()), comms_db=db, dry_run=True)
    assert stats.total_scanned == 3
    conn = sqlite3.connect(db)
    # dry-run still bootstraps the schema (watermark read), but writes no rows.
    count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert count == 0


def test_ingest_bootstraps_schema_on_empty_db(tmp_path):
    """The headline fix: ingest against a DB with no tables must not crash."""
    db = tmp_path / "fresh.db"
    assert not db.exists()
    stats = sl.ingest(client=_client(_basic_transport()), comms_db=db)
    assert stats.inserted == 3
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"messages", "conversations"} <= tables
