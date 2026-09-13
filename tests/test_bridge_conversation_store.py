"""The bridge owns its conversation store (aos#235).

`activity_client.py` was a literal no-op from the Qareen decommission onward —
every handler called it on every message for five months and nothing was
written anywhere. The aos-app activity feed it was a placeholder for is gone
too (the desktop app was retired in migration 118), so the bridge stores its
own traffic: one local SQLite table, no service, no network.

These tests pin the contract: the declared schema, a row per direction,
redaction applied at write time (the store is not a place to keep the
operator's phone numbers), and a schema creation that is safe to repeat.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STORE_PATH = REPO / "core" / "services" / "bridge" / "conversation_store.py"

REQUIRED_COLUMNS = {
    "id", "ts", "direction", "chat_id", "topic", "kind", "text_redacted", "meta_json",
}


def _load_store(db_path: Path):
    """Fresh module instance bound to an isolated DB."""
    spec = importlib.util.spec_from_file_location(
        f"conversation_store_under_test_{db_path.parent.name}", STORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.DB_PATH = db_path
    return mod


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "bridge.db"
    monkeypatch.setenv("AOS_BRIDGE_DB", str(db))
    mod = _load_store(db)
    mod.db_path = lambda: db  # belt and braces: no path resolution to $HOME
    return mod


def _rows(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
    finally:
        conn.close()


def _count(db: Path) -> int:
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


# ── The done-when: 0 → 2 rows, redacted ─────────────────────────────────────

def test_one_inbound_and_one_outbound_make_two_redacted_rows(store, tmp_path):
    db = tmp_path / "bridge.db"
    assert _count(db) == 0

    store.record_inbound("call me on +1 416-555-0199", chat_id=42, topic="dm")
    store.record_outbound("emailing sam@example.com now", chat_id=42, topic="dm")

    assert _count(db) == 2

    rows = _rows(db)
    assert [r["direction"] for r in rows] == ["in", "out"]
    assert "416-555-0199" not in rows[0]["text_redacted"]
    assert "[phone]" in rows[0]["text_redacted"]
    assert "sam@example.com" not in rows[1]["text_redacted"]
    assert "[email]" in rows[1]["text_redacted"]
    # The surrounding words survive — redaction, not deletion.
    assert "call me on" in rows[0]["text_redacted"]
    assert "now" in rows[1]["text_redacted"]


# ── Schema ──────────────────────────────────────────────────────────────────

def test_schema_has_the_declared_columns(store, tmp_path):
    db = tmp_path / "bridge.db"
    store.record_inbound("hello")
    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(messages)")}
    assert REQUIRED_COLUMNS <= cols, f"missing: {REQUIRED_COLUMNS - cols}"


def test_schema_creation_is_repeatable(store, tmp_path):
    db = tmp_path / "bridge.db"
    store.ensure_schema()
    store.ensure_schema()
    store.record_inbound("hello")
    store.ensure_schema()
    assert _count(db) == 1


def test_rows_carry_topic_kind_and_meta(store, tmp_path):
    db = tmp_path / "bridge.db"
    store.record_inbound("a voice note", chat_id=7, topic="daily", kind="voice",
                         meta={"duration_s": 12})
    row = _rows(db)[0]
    assert row["chat_id"] == 7
    assert row["topic"] == "daily"
    assert row["kind"] == "voice"
    assert json.loads(row["meta_json"])["duration_s"] == 12
    assert row["ts"]


# ── Redaction ───────────────────────────────────────────────────────────────

# Example data only: reserved documentation domains (example.com/.org) and NANP
# 555-01XX numbers, which are reserved for fiction and can never be assigned.
# privacy-scan enforces this — a realistic-looking address or number in a fixture
# is indistinguishable from a leaked one.
@pytest.mark.parametrize("raw", [
    "sam@example.com",
    "first.last+tag@sub.example.com",
    "reach me at WORK@Example.ORG please",
])
def test_emails_are_redacted(store, raw):
    out = store.redact(raw)
    assert "@" not in out
    assert "[email]" in out


@pytest.mark.parametrize("raw", [
    "+1 416-555-0199",
    "(416) 555-0199",
    "416-555-0199",
    "+1 416 555 0142",
    "+14165550199",
])
def test_phone_numbers_are_redacted(store, raw):
    out = store.redact(f"ring {raw} tonight")
    assert "[phone]" in out, out
    assert "555" not in out.replace("[phone]", "")


@pytest.mark.parametrize("keep", [
    "the suite has 1730 tests",
    "disk is at 91.2% full",
    "2026-09-13 is the date",
    "2026-09-13T01:30:00 was the timestamp",
    "port 4098 never came up",
    "task aos#235 is done",
])
def test_ordinary_numbers_survive(store, keep):
    assert store.redact(keep) == keep


def test_redaction_happens_at_write_not_read(store, tmp_path):
    db = tmp_path / "bridge.db"
    store.record_inbound("my number is +1 416 555 0199")
    raw = sqlite3.connect(str(db)).execute("SELECT text_redacted FROM messages").fetchone()[0]
    assert "0199" not in raw, "unredacted text reached the DB file"


def test_none_and_empty_text_are_safe(store, tmp_path):
    db = tmp_path / "bridge.db"
    store.record_inbound("")
    store.record_outbound(None)
    assert _count(db) == 2


# ── Call-site contract: the no-op module is gone ────────────────────────────

def test_activity_client_no_op_is_gone():
    assert not (REPO / "core" / "services" / "bridge" / "activity_client.py").exists(), \
        "the no-op activity_client was replaced by conversation_store — don't leave dead calls"


def test_no_bridge_module_still_imports_activity_client():
    bridge = REPO / "core" / "services" / "bridge"
    offenders = [
        p.name for p in bridge.glob("*.py")
        if "import activity_client" in p.read_text()
        or "from activity_client" in p.read_text()
    ]
    assert not offenders, f"still importing the removed no-op: {offenders}"


def test_inbound_and_outbound_are_recorded_by_the_telegram_handler():
    """The store is only useful if the handler actually calls it."""
    src = (REPO / "core" / "services" / "bridge" / "telegram_channel.py").read_text()
    assert "record_inbound(" in src
    assert "record_outbound(" in src
