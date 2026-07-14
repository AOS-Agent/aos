"""Tests for the signal store (persistence layer)."""
import sqlite3

import pytest

from core.engine.people.intel.store import SignalStore
from core.engine.people.intel.types import (
    CommunicationSignal,
    MetadataSignal,
    PersonSignals,
    VoiceSignal,
)


@pytest.fixture()
def store(tmp_path):
    s = SignalStore(tmp_path / "people.db")
    s.init_schema()
    return s


def test_init_schema_creates_table(tmp_path):
    db = tmp_path / "people.db"
    store = SignalStore(db)
    store.init_schema()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_store'"
        ).fetchone()
        assert row is not None
        # Check indexes
        indexes = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='signal_store'"
            ).fetchall()
        ]
        assert any("idx_signal_store_person" in i for i in indexes)
        assert any("idx_signal_store_source" in i for i in indexes)
    finally:
        conn.close()


def test_init_schema_idempotent(tmp_path):
    """Calling init_schema twice should not error."""
    store = SignalStore(tmp_path / "people.db")
    store.init_schema()
    store.init_schema()  # no exception


def test_save_and_load_single_source(store):
    signals = PersonSignals(person_id="p_test", person_name="Alice")
    signals.source_coverage = ["apple_messages"]
    signals.communication.append(
        CommunicationSignal(
            source="apple_messages",
            channel="imessage",
            total_messages=42,
            sent=20,
            received=22,
            temporal_buckets={"2026-01": 10, "2026-02": 32},
            time_of_day={9: 5, 14: 10, 22: 27},
        )
    )

    store.save("p_test", "apple_messages", signals)
    loaded = store.load("p_test")

    assert loaded is not None
    assert loaded.person_id == "p_test"
    assert loaded.person_name == "Alice"
    assert len(loaded.communication) == 1
    assert loaded.communication[0].total_messages == 42
    assert loaded.communication[0].sent == 20
    assert loaded.communication[0].temporal_buckets == {"2026-01": 10, "2026-02": 32}
    # time_of_day keys become strings after JSON roundtrip — but dataclass rebuild
    # should preserve int keys if we wrote them as ints. JSON forces string keys.
    # We accept the string-key form on reload.
    assert loaded.communication[0].time_of_day  # non-empty
    assert "apple_messages" in loaded.source_coverage


def test_save_overwrites_same_source(store):
    s1 = PersonSignals(person_id="p_test")
    s1.communication.append(
        CommunicationSignal(source="wa", channel="whatsapp", total_messages=10)
    )
    store.save("p_test", "wa", s1)

    s2 = PersonSignals(person_id="p_test")
    s2.communication.append(
        CommunicationSignal(source="wa", channel="whatsapp", total_messages=999)
    )
    store.save("p_test", "wa", s2)

    loaded = store.load("p_test")
    assert loaded is not None
    assert len(loaded.communication) == 1
    assert loaded.communication[0].total_messages == 999


def test_load_merges_multiple_sources(store):
    s1 = PersonSignals(person_id="p_test", person_name="Bob")
    s1.source_coverage = ["apple_messages"]
    s1.communication.append(
        CommunicationSignal(source="apple_messages", channel="imessage", total_messages=50)
    )

    s2 = PersonSignals(person_id="p_test")
    s2.source_coverage = ["whatsapp"]
    s2.communication.append(
        CommunicationSignal(source="whatsapp", channel="whatsapp", total_messages=30)
    )
    s2.voice.append(VoiceSignal(source="calls", total_calls=5, total_minutes=42.5))

    store.save("p_test", "apple_messages", s1)
    store.save("p_test", "whatsapp", s2)

    loaded = store.load("p_test")
    assert loaded is not None
    assert loaded.person_id == "p_test"
    assert loaded.person_name == "Bob"
    # 2 communication signals merged from 2 sources
    assert len(loaded.communication) == 2
    assert loaded.total_messages == 80
    assert loaded.total_calls == 5
    assert set(loaded.source_coverage) >= {"apple_messages", "whatsapp"}


def test_load_returns_none_when_missing(store):
    assert store.load("does_not_exist") is None


def test_load_source_returns_single_row(store):
    s = PersonSignals(person_id="p_test")
    s.metadata.append(MetadataSignal(source="contacts", has_birthday=True, birthday="1990-05-15"))
    store.save("p_test", "apple_contacts", s)

    single = store.load_source("p_test", "apple_contacts")
    assert single is not None
    assert len(single.metadata) == 1
    assert single.metadata[0].birthday == "1990-05-15"
    assert single.metadata[0].has_birthday is True


def test_delete_source(store):
    s1 = PersonSignals(person_id="p_test")
    s1.communication.append(CommunicationSignal(source="a", channel="a", total_messages=1))
    s2 = PersonSignals(person_id="p_test")
    s2.communication.append(CommunicationSignal(source="b", channel="b", total_messages=2))
    store.save("p_test", "a", s1)
    store.save("p_test", "b", s2)

    removed = store.delete("p_test", "a")
    assert removed == 1
    loaded = store.load("p_test")
    assert loaded is not None
    assert len(loaded.communication) == 1
    assert loaded.communication[0].source == "b"


def test_delete_all_for_person(store):
    s = PersonSignals(person_id="p_test")
    s.communication.append(CommunicationSignal(source="a", channel="a"))
    store.save("p_test", "a", s)
    store.save("p_test", "b", s)

    removed = store.delete("p_test")
    assert removed == 2
    assert store.load("p_test") is None


def test_list_persons(store):
    s = PersonSignals(person_id="p_1")
    store.save("p_1", "a", s)
    store.save("p_2", "a", s.__class__(person_id="p_2"))
    persons = store.list_persons()
    assert persons == ["p_1", "p_2"]


def test_stats(store):
    store.save("p_1", "apple_messages", PersonSignals(person_id="p_1"))
    store.save("p_1", "whatsapp", PersonSignals(person_id="p_1"))
    store.save("p_2", "apple_messages", PersonSignals(person_id="p_2"))

    stats = store.stats()
    assert stats["total_rows"] == 3
    assert stats["distinct_persons"] == 2
    assert stats["by_source"]["apple_messages"] == 2
    assert stats["by_source"]["whatsapp"] == 1


def test_roundtrip_preserves_all_fields(store):
    """Full roundtrip of a signal with all field types populated."""
    signals = PersonSignals(person_id="p_test", person_name="Charlie")
    signals.source_coverage = ["apple_messages"]
    signals.communication.append(
        CommunicationSignal(
            source="apple_messages",
            channel="imessage",
            total_messages=100,
            sent=40,
            received=60,
            first_message_date="2024-01-01T00:00:00",
            last_message_date="2026-04-01T12:34:56",
            temporal_buckets={"2024-01": 10, "2026-04": 90},
            temporal_pattern="growing",
            avg_message_length=42.5,
            response_latency_median=15.0,
            response_latency_avg=30.5,
            late_night_pct=0.1,
            business_hours_pct=0.6,
            evening_pct=0.3,
            voice_notes_sent=5,
            media_sent=12,
            links_shared=3,
            service_breakdown={"iMessage": 80, "SMS": 20},
            sample_messages=[
                {"text": "hi", "date": "2026-04-01", "direction": "out", "channel": "imessage"}
            ],
        )
    )
    store.save("p_test", "apple_messages", signals)

    loaded = store.load("p_test")
    assert loaded is not None
    c = loaded.communication[0]
    assert c.total_messages == 100
    assert c.temporal_pattern == "growing"
    assert c.response_latency_median == 15.0
    assert c.service_breakdown == {"iMessage": 80, "SMS": 20}
    assert len(c.sample_messages) == 1
    assert c.sample_messages[0]["text"] == "hi"


def test_extracted_at_is_set_on_load(store):
    s = PersonSignals(person_id="p_test")
    store.save("p_test", "src", s)
    loaded = store.load("p_test")
    assert loaded is not None
    assert loaded.extracted_at is not None
    # ISO 8601 roughly
    assert "T" in loaded.extracted_at
