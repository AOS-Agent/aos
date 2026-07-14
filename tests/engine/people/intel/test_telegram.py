"""Tests for the Telegram signal adapter."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.telegram import TelegramAdapter
from core.engine.people.intel.types import PersonSignals


# ── Timestamps used by fixtures ───────────────────────────────────────
# All UTC to keep assertions deterministic regardless of host timezone.

JAN_TS = datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc).timestamp()
FEB_TS = datetime(2026, 2, 20, 9, 15, tzinfo=timezone.utc).timestamp()
MAR_TS = datetime(2026, 3, 5, 20, 0, tzinfo=timezone.utc).timestamp()


def _write_fixture(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _line(obj: dict) -> str:
    return json.dumps(obj)


@pytest.fixture
def person_index() -> dict[str, dict]:
    return {
        "p1": {"name": "Alice Smith"},
        "p2": {"name": "SamTaylor"},  # camelCase canonical — tests variant match
    }


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    """Six-line fixture covering all adapter scenarios."""
    fp = tmp_path / "telegram-messages.jsonl"
    lines = [
        # 1. from_me=true — operator to bridge, should be ignored.
        _line(
            {
                "id": "tg-1",
                "chat_id": 111,
                "from_user": {
                    "id": 999,
                    "first_name": "Operator",
                    "last_name": "",
                    "username": None,
                },
                "text": "self note",
                "timestamp": JAN_TS,
                "from_me": True,
                "thread_id": None,
            }
        ),
        # 2. Alice — Jan message (plain text).
        _line(
            {
                "id": "tg-2",
                "chat_id": 222,
                "from_user": {
                    "id": 2222,
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "username": "alice_s",
                },
                "text": "hey",
                "timestamp": JAN_TS,
                "from_me": False,
                "thread_id": None,
            }
        ),
        # 3. MALFORMED — double comma, must be skipped without crashing.
        '{"id":"tg-bad","chat_id":"test","from_user":{"id":3,"first_name":"X","last_name":"Y","username":null},"text":"oops","timestamp":1774557690.211617,,"from_me":false,"thread_id":null}',
        # 4. Alice — Feb message with a link.
        _line(
            {
                "id": "tg-4",
                "chat_id": 222,
                "from_user": {
                    "id": 2222,
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "username": "alice_s",
                },
                "text": "check http://example.com",
                "timestamp": FEB_TS,
                "from_me": False,
                "thread_id": None,
            }
        ),
        # 5. Bob — no match in person_index, should be ignored.
        _line(
            {
                "id": "tg-5",
                "chat_id": 333,
                "from_user": {
                    "id": 5555,
                    "first_name": "Bob",
                    "last_name": "Jones",
                    "username": "bobj",
                },
                "text": "hello from bob",
                "timestamp": FEB_TS,
                "from_me": False,
                "thread_id": None,
            }
        ),
        # 6. Sam Taylor — matches camelCase canonical "SamTaylor".
        _line(
            {
                "id": "tg-6",
                "chat_id": 444,
                "from_user": {
                    "id": 6666,
                    "first_name": "Sam",
                    "last_name": "Taylor",
                    "username": "alin",
                },
                "text": "assalamu alaykum",
                "timestamp": MAR_TS,
                "from_me": False,
                "thread_id": None,
            }
        ),
    ]
    _write_fixture(fp, lines)
    return fp


# ── Availability ──────────────────────────────────────────────────────


def test_is_available_true_false(tmp_path: Path, fixture_path: Path) -> None:
    present = TelegramAdapter(jsonl_path=str(fixture_path))
    assert present.is_available() is True

    missing = TelegramAdapter(jsonl_path=str(tmp_path / "nope.jsonl"))
    assert missing.is_available() is False


# ── Extraction basics ─────────────────────────────────────────────────


def test_extract_returns_dict(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"p1", "p2"}
    for sigs in result.values():
        assert isinstance(sigs, PersonSignals)


def test_skips_from_me_messages(fixture_path: Path, person_index: dict) -> None:
    """from_me=true operator messages must never become signals."""
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    # There is no operator in person_index, but even if there were, the
    # from_me message shouldn't appear in anyone's bucket. The only inbound
    # "Alice" messages are the two that should be counted.
    alice = result["p1"]
    assert alice.communication[0].total_messages == 2


def test_skips_malformed_lines(fixture_path: Path, person_index: dict) -> None:
    """Malformed JSON lines must not crash — other lines still process."""
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    # If the malformed line crashed us, we'd see 0 or raise; we should see
    # both p1 and p2 populated normally.
    assert "p1" in result
    assert "p2" in result
    assert result["p1"].communication[0].total_messages == 2


def test_matches_by_full_name(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    alice = result["p1"]
    assert alice.person_name == "Alice Smith"
    assert alice.communication[0].total_messages == 2
    assert alice.source_coverage == ["telegram"]


def test_matches_camelcase_canonical_name(
    fixture_path: Path, person_index: dict
) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    ali = result["p2"]
    assert ali.communication[0].total_messages == 1
    assert ali.communication[0].sample_messages[0]["text"] == "assalamu alaykum"


def test_total_messages_counted(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    comm = result["p1"].communication[0]
    assert comm.total_messages == 2
    assert comm.received == 2
    assert comm.sent == 0


def test_links_counted(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    comm = result["p1"].communication[0]
    assert comm.links_shared == 1


def test_temporal_buckets_populated(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    buckets = result["p1"].communication[0].temporal_buckets
    assert set(buckets.keys()) == {"2026-01", "2026-02"}
    assert buckets["2026-01"] == 1
    assert buckets["2026-02"] == 1


def test_time_of_day_populated(fixture_path: Path, person_index: dict) -> None:
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    tod = result["p1"].communication[0].time_of_day
    assert sum(tod.values()) == 2
    # Jan msg is 14:30 UTC → hour 14; Feb msg is 09:15 → hour 9.
    assert tod.get(14) == 1
    assert tod.get(9) == 1


def test_sample_messages_capped_at_5(
    tmp_path: Path, person_index: dict
) -> None:
    """Sample messages list is capped at 5 and sorted by recency."""
    fp = tmp_path / "many.jsonl"
    lines = []
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    for i in range(8):
        lines.append(
            _line(
                {
                    "id": f"tg-{i}",
                    "chat_id": 222,
                    "from_user": {
                        "id": 2222,
                        "first_name": "Alice",
                        "last_name": "Smith",
                        "username": "alice_s",
                    },
                    "text": f"msg-{i}",
                    "timestamp": base + i * 3600,
                    "from_me": False,
                    "thread_id": None,
                }
            )
        )
    _write_fixture(fp, lines)
    adapter = TelegramAdapter(jsonl_path=str(fp))
    result = adapter.extract_all(person_index)
    comm = result["p1"].communication[0]
    assert comm.total_messages == 8
    assert len(comm.sample_messages) == 5
    # Most recent first → msg-7
    assert comm.sample_messages[0]["text"] == "msg-7"
    assert comm.sample_messages[-1]["text"] == "msg-3"


def test_unmatched_user_absent(fixture_path: Path, person_index: dict) -> None:
    """Bob has no match in person_index — must not appear in result."""
    adapter = TelegramAdapter(jsonl_path=str(fixture_path))
    result = adapter.extract_all(person_index)
    for sigs in result.values():
        assert "Bob" not in sigs.person_name


def test_returns_empty_when_all_from_me(
    tmp_path: Path, person_index: dict
) -> None:
    fp = tmp_path / "only-me.jsonl"
    lines = [
        _line(
            {
                "id": "tg-x1",
                "chat_id": 111,
                "from_user": {
                    "id": 999,
                    "first_name": "Operator",
                    "last_name": "",
                    "username": None,
                },
                "text": "control",
                "timestamp": JAN_TS,
                "from_me": True,
                "thread_id": None,
            }
        ),
        _line(
            {
                "id": "tg-x2",
                "chat_id": 111,
                "from_user": {
                    "id": 999,
                    "first_name": "Operator",
                    "last_name": "",
                    "username": None,
                },
                "text": "another",
                "timestamp": FEB_TS,
                "from_me": True,
                "thread_id": None,
            }
        ),
    ]
    _write_fixture(fp, lines)
    adapter = TelegramAdapter(jsonl_path=str(fp))
    assert adapter.extract_all(person_index) == {}


def test_extract_returns_empty_when_file_missing(
    tmp_path: Path, person_index: dict
) -> None:
    adapter = TelegramAdapter(jsonl_path=str(tmp_path / "does-not-exist.jsonl"))
    assert adapter.extract_all(person_index) == {}
