"""Tests for the Gmail ingest adapter.

No real mail lives in this file — every message is a synthetic fixture
built by ``_gmail_msg`` with base64url-encoded bodies, mirroring the shape
of a real ``gmail.users.messages.get(format=full)`` response.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.engine.comms.channels import gmail_ingest as gi

# ── fixtures ─────────────────────────────────────────────────────────────


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _gmail_msg(
    mid, thread, internal_ms, frm, to, subject,
    *, plain=None, html=None, labels=None, cc=None, attach=False,
):
    headers = [
        {"name": "From", "value": frm},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{mid}@mail.example>"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    if attach:
        parts.append({
            "mimeType": "application/pdf", "filename": "doc.pdf",
            "body": {"attachmentId": "att1", "size": 10},
        })
    payload = {
        "mimeType": "multipart/mixed" if parts else "text/plain",
        "headers": headers,
        "body": {},
        "parts": parts,
    }
    return {
        "id": mid, "threadId": thread, "internalDate": str(internal_ms),
        "labelIds": labels or ["INBOX"], "snippet": "a snippet",
        "payload": payload,
    }


def _make_runner(messages: dict):
    """Fake gws runner: (args) -> (rc, stdout, stderr)."""
    def runner(args):
        verb = args[3]  # gmail users messages <verb>
        if verb == "list":
            page = json.dumps({"messages": [{"id": i} for i in messages]})
            return (0, page + "\n", "")
        if verb == "get":
            params = json.loads(args[args.index("--params") + 1])
            return (0, json.dumps(messages[params["id"]]), "")
        return (1, "", "unknown verb")
    return runner


def _make_comms_db(path: Path):
    """Build a comms.db that mirrors production, INCLUDING the messages_fts
    virtual table and its triggers — so an insert-count that naively used
    ``conn.total_changes`` (inflated by the trigger writes) would be caught."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, channel TEXT, direction TEXT,
            sender_id TEXT, recipient_id TEXT, content TEXT, timestamp TEXT,
            thread_id TEXT, reply_to_id TEXT,
            has_attachment INTEGER DEFAULT 0, attachment_type TEXT,
            attachment_path TEXT, processed INTEGER DEFAULT 0,
            channel_metadata TEXT, person_id TEXT, conversation_id TEXT,
            intent TEXT, urgency INTEGER DEFAULT 0
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        """
    )
    conn.commit()
    conn.close()


# ── pure helpers ─────────────────────────────────────────────────────────


def test_html_to_text_strips_markup_and_scripts():
    html = "<html><style>x{}</style><body><p>Hello</p><script>bad()</script>" \
           "<div>World</div></body></html>"
    out = gi._html_to_text(html)
    assert "Hello" in out and "World" in out
    assert "bad()" not in out and "{" not in out


def test_extract_body_prefers_plain_and_truncates():
    payload = _gmail_msg(
        "m", "t", 1, "a@example.com", "b@example.org", "s",
        plain="PLAINTEXT", html="<p>HTMLTEXT</p>",
    )["payload"]
    text, truncated = gi.extract_body(payload)
    assert text == "PLAINTEXT" and truncated is False

    big = "z" * (gi.BODY_CAP + 500)
    payload2 = _gmail_msg("m", "t", 1, "a@example.com", "b@example.org", "s", plain=big)["payload"]
    text2, truncated2 = gi.extract_body(payload2)
    assert len(text2) == gi.BODY_CAP and truncated2 is True


def test_parse_addr_and_subject_normalization():
    assert gi.parse_addr('"Jane Doe" <Jane@Example.COM>') == "jane@example.com"
    assert gi.parse_addrs("a@example.com, b@example.org") == ["a@example.com", "b@example.org"]
    assert gi.normalize_subject("Re: Fwd:  Hello  World") == "hello world"


def test_ts_from_internal_date_is_millis():
    ts = gi._ts_from_internal_date("1700000000000")
    assert isinstance(ts, datetime)
    assert gi._ts_from_internal_date("garbage") is None


# ── map_message ──────────────────────────────────────────────────────────


def test_map_inbound_message():
    raw = _gmail_msg(
        "abc", "thr1", 1_700_000_000_000, "friend@example.org",
        "me@example.net", "Project update", plain="body here", labels=["INBOX"],
    )
    row = gi.map_message(raw, "me@example.net", {"me@example.net"})
    assert row["id"] == "gmail:abc"
    assert row["channel"] == "email"
    assert row["direction"] == "inbound"
    assert row["sender_id"] == "friend@example.org"
    assert row["recipient_id"] == "me"
    assert row["thread_id"] == "thr1"
    assert row["content"].startswith("Project update")
    assert "body here" in row["content"]
    meta = json.loads(row["channel_metadata"])
    assert meta["labels"] == ["INBOX"]
    assert meta["account"] == "me@example.net"
    assert row["_counterpart"] == "friend@example.org"


def test_map_outbound_message():
    raw = _gmail_msg(
        "def", "thr2", 1_700_000_000_000, "me@example.net",
        "client@example.org", "Reply", plain="ok", labels=["SENT"],
    )
    row = gi.map_message(raw, "me@example.net", {"me@example.net"})
    assert row["direction"] == "outbound"
    assert row["sender_id"] == "me"
    assert row["recipient_id"] == "client@example.org"
    assert row["_counterpart"] == "client@example.org"


def test_has_attachment_detected():
    raw = _gmail_msg("g", "t", 1, "a@example.com", "b@example.org", "s", plain="x", attach=True)
    row = gi.map_message(raw, "b@example.org", {"b@example.org"})
    assert row["has_attachment"] == 1


def test_is_spam():
    assert gi.is_spam(_gmail_msg("s", "t", 1, "a@example.com", "b@example.org", "s", labels=["SPAM"]))
    assert gi.is_spam(_gmail_msg("s", "t", 1, "a@example.com", "b@example.org", "s", labels=["TRASH"]))
    assert not gi.is_spam(_gmail_msg("s", "t", 1, "a@example.com", "b@example.org", "s", labels=["INBOX"]))


# ── query / watermark / state ────────────────────────────────────────────


def test_build_query_excludes_spam_and_applies_cutoff():
    q_none = gi._build_query(None, None)
    assert "-in:spam" in q_none and "-in:trash" in q_none and "after:" not in q_none

    since = datetime(2026, 4, 1)
    q_since = gi._build_query(since, None)
    assert f"after:{int(since.timestamp())}" in q_since

    # watermark path: 60s safety overlap subtracted
    wm_ms = 1_700_000_000_000
    q_wm = gi._build_query(None, wm_ms)
    assert f"after:{wm_ms // 1000 - 60}" in q_wm


def test_state_roundtrip(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(gi, "STATE_FILE", state_file)
    gi._save_state({"a@example.com": {"watermark_ms": 123}})
    assert gi._load_state()["a@example.com"]["watermark_ms"] == 123


# ── Apple cross-source dedup ─────────────────────────────────────────────


def test_apple_dedup_index_and_match(tmp_path):
    db = tmp_path / "comms.db"
    _make_comms_db(db)
    conn = sqlite3.connect(db)
    ts = datetime(2026, 4, 5, 10, 30, 0)
    conn.execute(
        "INSERT INTO messages (id, channel, direction, sender_id, recipient_id, "
        "content, timestamp, channel_metadata) VALUES (?,?,?,?,?,?,?,?)",
        ("em_1", "email", "inbound", "friend@example.org", "me",
         "Hello", ts.isoformat(),
         json.dumps({"subject": "Re: Meeting", "sender_address": "friend@example.org"})),
    )
    conn.commit()
    index = gi._apple_dup_index(conn, datetime(2026, 4, 30))
    conn.close()

    # Same subject+counterpart within +40s → duplicate.
    row = {
        "_subject_norm": gi.normalize_subject("Meeting"),
        "_counterpart": "friend@example.org",
        "_ts": ts.replace(second=40),
    }
    assert gi._is_apple_dup(row, index) is True

    # Different counterpart → not a duplicate.
    row2 = dict(row, _counterpart="someone@example.com")
    assert gi._is_apple_dup(row2, index) is False


# ── end-to-end ingest_account ────────────────────────────────────────────


def _run_ingest(tmp_path, messages, **kwargs):
    comms = tmp_path / "comms.db"
    people = tmp_path / "people.db"
    _make_comms_db(comms)
    client = gi.GwsGmail("me@example.net", {}, runner=_make_runner(messages))
    return gi.ingest_account(
        "me@example.net", {}, client=client, comms_db=comms, people_db=people,
        self_accounts={"me@example.net"}, state=kwargs.pop("state", {}),
        **kwargs,
    ), comms


def test_ingest_inserts_and_skips_spam(tmp_path):
    messages = {
        "m1": _gmail_msg("m1", "t1", 1_700_000_000_000, "a@example.org",
                         "me@example.net", "Hi", plain="body one", labels=["INBOX"]),
        "m2": _gmail_msg("m2", "t2", 1_700_000_100_000, "spammer@example.org",
                         "me@example.net", "Win", plain="junk", labels=["SPAM"]),
    }
    state = {}
    stats, comms = _run_ingest(tmp_path, messages, state=state)
    assert stats.inserted == 1
    assert stats.skipped_spam == 1
    conn = sqlite3.connect(comms)
    rows = conn.execute("SELECT id, direction FROM messages").fetchall()
    conn.close()
    assert rows == [("gmail:m1", "inbound")]
    # Watermark advanced to the max internalDate seen (incl. the spam one).
    assert state["me@example.net"]["watermark_ms"] == 1_700_000_100_000


def test_ingest_is_idempotent(tmp_path):
    messages = {
        "m1": _gmail_msg("m1", "t1", 1_700_000_000_000, "a@example.org",
                         "me@example.net", "Hi", plain="body", labels=["INBOX"]),
    }
    comms = tmp_path / "comms.db"
    people = tmp_path / "people.db"
    _make_comms_db(comms)
    state = {}

    def go():
        client = gi.GwsGmail("me@example.net", {}, runner=_make_runner(messages))
        return gi.ingest_account(
            "me@example.net", {}, client=client, comms_db=comms, people_db=people,
            self_accounts={"me@example.net"}, state=state,
        )

    first = go()
    second = go()
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.skipped_existing == 1
    conn = sqlite3.connect(comms)
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    conn.close()


def test_ingest_skips_apple_duplicate(tmp_path):
    comms = tmp_path / "comms.db"
    people = tmp_path / "people.db"
    _make_comms_db(comms)
    ts = datetime(2026, 4, 5, 9, 0, 0)
    conn = sqlite3.connect(comms)
    conn.execute(
        "INSERT INTO messages (id, channel, direction, sender_id, recipient_id, "
        "content, timestamp, channel_metadata) VALUES (?,?,?,?,?,?,?,?)",
        ("em_1", "email", "inbound", "friend@example.org", "me", "Hello",
         ts.isoformat(),
         json.dumps({"subject": "Lunch", "sender_address": "friend@example.org"})),
    )
    conn.commit()
    conn.close()

    internal_ms = int(ts.timestamp() * 1000)
    messages = {
        "m1": _gmail_msg("m1", "t1", internal_ms, "friend@example.org",
                         "me@example.net", "Lunch", plain="see you", labels=["INBOX"]),
    }
    client = gi.GwsGmail("me@example.net", {}, runner=_make_runner(messages))
    stats = gi.ingest_account(
        "me@example.net", {}, client=client, comms_db=comms, people_db=people,
        self_accounts={"me@example.net"}, state={},
    )
    assert stats.skipped_apple_dup == 1
    assert stats.inserted == 0


def test_ingest_dry_run_writes_nothing(tmp_path):
    messages = {
        "m1": _gmail_msg("m1", "t1", 1_700_000_000_000, "a@example.org",
                         "me@example.net", "Hi", plain="body", labels=["INBOX"]),
    }
    stats, comms = _run_ingest(tmp_path, messages, dry_run=True)
    conn = sqlite3.connect(comms)
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    conn.close()
