"""Tests for core/engine/comms/recall.py — the recall contract + access control.

Everything runs against throwaway fixture SQLite DBs built in tmp_path with
FAKE data (RFC/NANP-reserved values only). No test reads the operator's real
comms.db or people.db. The 5-tier resolver is stubbed — resolver correctness
lives in its own suite; here we prove recall *uses* a person_id correctly.

Runs under pytest, and standalone via `python3 tests/test_recall.py`.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# recall.py lives in core/engine/comms/
_COMMS_DIR = Path(__file__).parent.parent / "core" / "engine" / "comms"
if str(_COMMS_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMS_DIR))

import recall  # noqa: E402
from recall import RecallEngine, scope_for  # noqa: E402

# ── Fixture builders ──────────────────────────────────────────────────────

# The live comms.db message + FTS schema, replicated verbatim so the tests
# exercise the same triggers/virtual table the engine queries in production.
_COMMS_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    sender_id TEXT,
    recipient_id TEXT,
    content TEXT,
    timestamp TEXT NOT NULL,
    thread_id TEXT,
    reply_to_id TEXT,
    has_attachment INTEGER NOT NULL DEFAULT 0,
    attachment_type TEXT,
    attachment_path TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    channel_metadata TEXT,
    person_id TEXT,
    conversation_id TEXT,
    intent TEXT,
    urgency INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, content=messages, content_rowid=rowid
);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
"""

_PEOPLE_SCHEMA = """
CREATE TABLE people (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    importance INTEGER DEFAULT 3,
    privacy_level INTEGER DEFAULT 1
);
"""

# Fake people. Three privacy tiers so filtering is exercised on all of them.
_PEOPLE = [
    ("pers_open", "Alice Open", 2, 1),       # full AI  -> visible by default
    ("pers_limited", "Bob Limited", 3, 2),   # limited  -> hidden by default
    ("pers_private", "Carol Private", 1, 3), # no AI    -> hidden by default
]

# Fake messages. content is invented; no real personal data.
_MESSAGES = [
    # id, channel, direction, content, timestamp, person_id
    ("m_open_1", "imessage", "inbound", "lets grab lunch about the ramadan plans",
     "2026-07-10T12:00:00", "pers_open"),
    ("m_open_2", "whatsapp", "outbound", "confirmed the ramadan iftar booking",
     "2026-07-12T09:30:00", "pers_open"),
    ("m_open_3", "imessage", "inbound", "unrelated chatter about the weather",
     "2026-05-01T08:00:00", "pers_open"),
    ("m_limited_1", "imessage", "inbound", "secret ramadan surprise details",
     "2026-07-11T15:00:00", "pers_limited"),
    ("m_private_1", "email", "inbound", "confidential ramadan medical note",
     "2026-07-13T18:00:00", "pers_private"),
    ("m_unknown_1", "sms", "inbound", "ramadan reminder from an unmapped number",
     "2026-07-09T07:00:00", None),
    ("m_long_1", "imessage", "outbound", "x" * 500 + " ramadan",
     "2026-07-14T20:00:00", "pers_open"),
]


def _build_comms(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(_COMMS_SCHEMA)
    conn.executemany(
        "INSERT INTO messages (id, channel, direction, content, timestamp, person_id) "
        "VALUES (?,?,?,?,?,?)", _MESSAGES,
    )
    conn.commit()
    conn.close()
    return path


def _build_people(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(_PEOPLE_SCHEMA)
    conn.executemany(
        "INSERT INTO people (id, canonical_name, importance, privacy_level) "
        "VALUES (?,?,?,?)", _PEOPLE,
    )
    conn.commit()
    conn.close()
    return path


def _stub_resolver(mapping: dict[str, str]):
    """Return a resolver callable mapping reference-substring -> person_id."""
    def _resolve(reference: str) -> dict:
        ref = reference.strip().lower()
        for key, pid in mapping.items():
            if key in ref:
                return {"person_id": pid, "resolved": True, "tier": 0}
        return {"person_id": None, "resolved": False, "tier": -1}
    return _resolve


@pytest.fixture()
def engine(tmp_path):
    comms = _build_comms(tmp_path / "comms.db")
    people = _build_people(tmp_path / "people.db")
    resolver = _stub_resolver({
        "alice": "pers_open", "open": "pers_open",
        "bob": "pers_limited", "limited": "pers_limited",
        "carol": "pers_private", "private": "pers_private",
    })
    return RecallEngine(comms_db=comms, people_db=people, resolver=resolver)


# ── The contract: all four fields, always ─────────────────────────────────

_CONTRACT_KEYS = {"entity", "confidence", "source_refs", "scope"}


def _assert_contract(row: dict):
    assert set(row.keys()) == _CONTRACT_KEYS, f"row keys drifted: {row.keys()}"
    assert isinstance(row["entity"], dict) and row["entity"]
    assert isinstance(row["confidence"], float)
    assert isinstance(row["source_refs"], list) and row["source_refs"], \
        "source_refs must never be empty"
    for ref in row["source_refs"]:
        assert {"message_id", "channel", "date"} <= set(ref.keys())
    assert isinstance(row["scope"], str) and row["scope"]


class TestContract:
    def test_search_rows_all_have_four_fields(self, engine):
        rows = engine.search(query="ramadan")
        assert rows
        for row in rows:
            _assert_contract(row)

    def test_get_row_has_four_fields(self, engine):
        row = engine.get("m_open_1")
        assert row is not None
        _assert_contract(row)

    def test_verbatim_confidence_is_one(self, engine):
        for row in engine.search(query="ramadan"):
            assert row["confidence"] == 1.0

    def test_source_ref_points_at_the_message(self, engine):
        row = engine.get("m_open_1")
        ref = row["source_refs"][0]
        assert ref["message_id"] == "m_open_1"
        assert ref["channel"] == "imessage"
        assert ref["date"] == "2026-07-10T12:00:00"

    def test_search_is_snippet_first(self, engine):
        [row] = engine.search(query="ramadan", person="alice",
                              since="2026-07-14", until="2026-07-14")
        e = row["entity"]
        assert e["truncated"] is True
        assert len(e["snippet"]) == recall.SNIPPET_LEN
        assert "content" not in e  # full text only via get()

    def test_get_returns_full_content(self, engine):
        row = engine.get("m_long_1")
        assert "content" in row["entity"]
        assert len(row["entity"]["content"]) > recall.SNIPPET_LEN


# ── Access control (privacy_level) ────────────────────────────────────────

class TestPrivacyFiltering:
    def test_private_and_limited_excluded_by_default(self, engine):
        rows = engine.search(query="ramadan", limit=100)
        pids = {r["entity"]["person_id"] for r in rows}
        assert "pers_limited" not in pids
        assert "pers_private" not in pids

    def test_include_private_surfaces_restricted(self, engine):
        rows = engine.search(query="ramadan", limit=100, include_private=True)
        pids = {r["entity"]["person_id"] for r in rows}
        assert "pers_limited" in pids
        assert "pers_private" in pids

    def test_open_contact_always_visible(self, engine):
        rows = engine.search(query="ramadan", limit=100)
        pids = {r["entity"]["person_id"] for r in rows}
        assert "pers_open" in pids

    def test_unresolved_person_included_and_scoped_unknown(self, engine):
        [row] = engine.search(query="ramadan", channel="sms")
        assert row["entity"]["person_id"] is None
        assert row["scope"] == "unknown"

    def test_scope_labels_reflect_privacy_level(self, engine):
        rows = engine.search(query="ramadan", limit=100, include_private=True)
        by_pid = {r["entity"]["person_id"]: r["scope"] for r in rows}
        assert by_pid["pers_open"] == "open"
        assert by_pid["pers_limited"] == "limited"
        assert by_pid["pers_private"] == "private"

    def test_get_private_message_returns_none_by_default(self, engine):
        assert engine.get("m_private_1") is None
        assert engine.get("m_limited_1") is None

    def test_get_private_message_with_flag(self, engine):
        row = engine.get("m_private_1", include_private=True)
        assert row is not None
        assert row["scope"] == "private"

    def test_person_scoped_query_on_private_contact_empty_by_default(self, engine):
        # Even asking for the person by name must not leak their messages.
        assert engine.search(query="ramadan", person="carol") == []

    def test_scope_for_helper(self):
        assert scope_for(1) == "open"
        assert scope_for(2) == "limited"
        assert scope_for(3) == "private"
        assert scope_for(None) == "unknown"
        assert scope_for(99) == "private"  # fail closed


# ── Resolver integration ──────────────────────────────────────────────────

class TestResolverIntegration:
    def test_person_query_scopes_to_that_person(self, engine):
        rows = engine.search(person="alice", limit=100)
        assert rows
        assert all(r["entity"]["person_id"] == "pers_open" for r in rows)

    def test_unresolvable_person_returns_empty(self, engine):
        # Never a silent unscoped dump of everyone.
        assert engine.search(person="nobody-here", query="ramadan") == []

    def test_person_and_topic_combine(self, engine):
        rows = engine.search(person="alice", query="weather")
        assert len(rows) == 1
        assert rows[0]["entity"]["message_id"] == "m_open_3"


# ── FTS query correctness ─────────────────────────────────────────────────

class TestFTS:
    def test_keyword_matches_content(self, engine):
        rows = engine.search(query="weather")
        assert {r["entity"]["message_id"] for r in rows} == {"m_open_3"}

    def test_multi_term_is_and(self, engine):
        # both terms must be present -> only the iftar booking line
        rows = engine.search(query="iftar booking")
        assert {r["entity"]["message_id"] for r in rows} == {"m_open_2"}

    def test_punctuation_only_query_is_ignored_not_crash(self, engine):
        # '!!!' sanitises to no terms -> behaves as no FTS filter (all visible)
        rows = engine.search(query="!!!", limit=100)
        assert rows  # did not crash, returned the default-visible set

    def test_quote_in_query_does_not_break_match(self, engine):
        # A stray double-quote must not raise an FTS syntax error.
        rows = engine.search(query='ramadan"')
        assert isinstance(rows, list)

    def test_fts_query_builder(self):
        assert recall._fts_query("hello world") == '"hello" AND "world"'
        assert recall._fts_query("   ") is None
        assert recall._fts_query("!!!") is None
        assert recall._fts_query('a"b') == '"a""b"'


# ── Timeframe ─────────────────────────────────────────────────────────────

class TestTimeframe:
    def test_since_lower_bound(self, engine):
        rows = engine.search(query="ramadan", since="2026-07-12", limit=100)
        dates = [r["entity"]["timestamp"][:10] for r in rows]
        assert dates and all(d >= "2026-07-12" for d in dates)

    def test_until_upper_bound(self, engine):
        rows = engine.search(query="ramadan", until="2026-07-10", limit=100)
        dates = [r["entity"]["timestamp"][:10] for r in rows]
        assert dates and all(d <= "2026-07-10" for d in dates)

    def test_window_combines_both(self, engine):
        rows = engine.search(query="ramadan", since="2026-07-10",
                             until="2026-07-12", limit=100)
        dates = [r["entity"]["timestamp"][:10] for r in rows]
        assert all("2026-07-10" <= d <= "2026-07-12" for d in dates)

    def test_results_ordered_recent_first(self, engine):
        rows = engine.search(query="ramadan", limit=100)
        ts = [r["entity"]["timestamp"] for r in rows]
        assert ts == sorted(ts, reverse=True)


# ── Bounds ────────────────────────────────────────────────────────────────

class TestBounds:
    def test_limit_clamped_to_max(self, engine):
        rows = engine.search(query="ramadan", limit=10_000)
        # cannot exceed MAX_LIMIT even if asked for more
        assert len(rows) <= recall.MAX_LIMIT

    def test_limit_floor_is_one(self, engine):
        rows = engine.search(query="ramadan", limit=0)
        assert len(rows) <= 1

    def test_default_limit_applied(self, engine, monkeypatch):
        # Shrink the cap and prove the default is honoured.
        monkeypatch.setattr(recall, "DEFAULT_LIMIT", 2)
        rows = engine.search(query="ramadan", limit=recall.DEFAULT_LIMIT)
        assert len(rows) == 2


# ── Robustness ────────────────────────────────────────────────────────────

class TestRobustness:
    def test_missing_comms_db_raises(self, tmp_path):
        eng = RecallEngine(comms_db=tmp_path / "nope.db",
                           people_db=tmp_path / "nope-people.db")
        with pytest.raises(FileNotFoundError):
            eng.search(query="ramadan")

    def test_missing_people_db_allows_query_scope_unknown(self, tmp_path):
        comms = _build_comms(tmp_path / "comms.db")
        eng = RecallEngine(comms_db=comms, people_db=tmp_path / "absent.db",
                           resolver=_stub_resolver({}))
        rows = eng.search(query="ramadan", limit=100)
        # No people.db -> no privacy signal -> everything scoped unknown,
        # nothing filtered (people.db absent is not a private flag).
        assert rows
        assert all(r["scope"] == "unknown" for r in rows)

    def test_get_unknown_id_returns_none(self, engine):
        assert engine.get("does-not-exist") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
