"""Batching + tiny-day merge."""
from __future__ import annotations

from core.engine.comms.enrich.batching import build_batches

from ._helpers import msg


def test_dense_day_stands_alone():
    msgs = [msg(f"m{i}", "hi", ts=f"2026-03-09T10:{i:02d}:00", person_id="p1")
            for i in range(20)]
    batches = build_batches(msgs, min_batch_msgs=15, max_batch_msgs=40)
    assert len(batches) == 1
    assert batches[0].n == 20
    assert batches[0].person_id == "p1"
    assert "2026-03-09" in batches[0].batch_key


def test_thin_days_merge_by_week():
    # Three thin days in the same ISO week for one person → one merged batch.
    msgs = []
    for day in ("2026-03-09", "2026-03-10", "2026-03-11"):
        for i in range(3):
            msgs.append(msg(f"{day}-{i}", "x", ts=f"{day}T09:0{i}:00", person_id="p1"))
    batches = build_batches(msgs, min_batch_msgs=15, max_batch_msgs=40)
    assert len(batches) == 1
    assert batches[0].n == 9
    assert "-W" in batches[0].batch_key  # week unit, not a date


def test_max_batch_chunking():
    msgs = [msg(f"m{i}", "hi", ts=f"2026-03-09T10:00:{i:02d}", person_id="p1")
            for i in range(90)]
    batches = build_batches(msgs, min_batch_msgs=15, max_batch_msgs=40)
    assert [b.n for b in batches] == [40, 40, 10]


def test_different_people_never_merge():
    msgs = [msg("a", "hi", ts="2026-03-09T10:00:00", person_id="p1"),
            msg("b", "hi", ts="2026-03-09T10:00:00", person_id="p2")]
    batches = build_batches(msgs, min_batch_msgs=15, max_batch_msgs=40)
    assert {b.person_id for b in batches} == {"p1", "p2"}
    assert len(batches) == 2


def test_unresolved_handles_stay_separate():
    # No person_id → bucket by counterpart handle, never lumped together.
    msgs = [msg("a", "hi", ts="2026-03-09T10:00:00", sender_id="handle-a@example"),
            msg("b", "hi", ts="2026-03-09T10:00:00", sender_id="handle-b@example")]
    batches = build_batches(msgs, min_batch_msgs=1, max_batch_msgs=40)
    assert len(batches) == 2
    assert all(b.person_id is None for b in batches)


def test_deterministic_batch_keys():
    msgs = [msg(f"m{i}", "hi", ts=f"2026-03-09T10:00:{i:02d}", person_id="p1")
            for i in range(20)]
    k1 = [b.batch_key for b in build_batches(msgs, min_batch_msgs=15, max_batch_msgs=40)]
    k2 = [b.batch_key for b in build_batches(list(reversed(msgs)), min_batch_msgs=15, max_batch_msgs=40)]
    assert k1 == k2  # order-independent, stable for idempotent resume
