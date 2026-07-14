"""Tests for the classification store and feedback loop."""
import sqlite3
from pathlib import Path

import pytest

from core.engine.people.intel.feedback import ClassificationStore
from core.engine.people.intel.taxonomy import (
    ClassificationResult,
    Tier,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> ClassificationStore:
    s = ClassificationStore(tmp_path / "people.db")
    s.init_schema()
    return s


def _result(
    person_id: str,
    tier: Tier = Tier.ACTIVE,
    tags: list[dict] | None = None,
    reasoning: str = "",
    model: str | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        person_id=person_id,
        tier=tier,
        context_tags=tags or [],
        reasoning=reasoning,
        model=model,
    )


# ── Schema ────────────────────────────────────────────────────────────


def test_init_schema_creates_tables(tmp_path: Path):
    store = ClassificationStore(tmp_path / "people.db")
    store.init_schema()
    conn = sqlite3.connect(str(store.db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "person_classification" in tables
    assert "classification_feedback" in tables


def test_init_schema_idempotent(tmp_path: Path):
    store = ClassificationStore(tmp_path / "people.db")
    store.init_schema()
    store.init_schema()  # no exception


# ── Classification save/load ─────────────────────────────────────────


def test_save_and_load(store: ClassificationStore):
    result = _result(
        "p_test",
        tier=Tier.CORE,
        tags=[{"tag": "family_nuclear", "confidence": 0.9}],
        reasoning="test reason",
        model="test-model",
    )
    store.save(result)

    loaded = store.load("p_test")
    assert loaded is not None
    assert loaded.person_id == "p_test"
    assert loaded.tier == Tier.CORE
    assert len(loaded.context_tags) == 1
    assert loaded.context_tags[0]["tag"] == "family_nuclear"
    assert loaded.reasoning == "test reason"
    assert loaded.model == "test-model"


def test_load_returns_none_for_missing(store: ClassificationStore):
    assert store.load("p_nonexistent") is None


def test_save_overwrites_existing(store: ClassificationStore):
    store.save(_result("p_test", tier=Tier.ACTIVE))
    store.save(_result("p_test", tier=Tier.CORE))

    loaded = store.load("p_test")
    assert loaded is not None
    assert loaded.tier == Tier.CORE


def test_load_many_empty(store: ClassificationStore):
    assert store.load_many([]) == []
    assert store.load_many() == []


def test_load_many_filter_by_ids(store: ClassificationStore):
    store.save(_result("p_a", tier=Tier.ACTIVE))
    store.save(_result("p_b", tier=Tier.CORE))
    store.save(_result("p_c", tier=Tier.DORMANT))

    results = store.load_many(["p_a", "p_c"])
    tiers = {r.person_id: r.tier for r in results}
    assert tiers == {"p_a": Tier.ACTIVE, "p_c": Tier.DORMANT}


def test_load_many_all(store: ClassificationStore):
    store.save(_result("p_a"))
    store.save(_result("p_b"))
    results = store.load_many()
    assert len(results) == 2


def test_tier_distribution(store: ClassificationStore):
    store.save(_result("p_1", tier=Tier.ACTIVE))
    store.save(_result("p_2", tier=Tier.ACTIVE))
    store.save(_result("p_3", tier=Tier.CORE))
    store.save(_result("p_4", tier=Tier.DORMANT))

    dist = store.tier_distribution()
    assert dist["active"] == 2
    assert dist["core"] == 1
    assert dist["dormant"] == 1


def test_delete(store: ClassificationStore):
    store.save(_result("p_test", tier=Tier.ACTIVE))
    removed = store.delete("p_test")
    assert removed == 1
    assert store.load("p_test") is None


def test_delete_missing_returns_zero(store: ClassificationStore):
    removed = store.delete("p_never_existed")
    assert removed == 0


# ── Feedback ──────────────────────────────────────────────────────────


def test_record_feedback_new_only(store: ClassificationStore):
    new = _result("p_x", tier=Tier.CORE, tags=[{"tag": "family_nuclear", "confidence": 1.0}])
    store.record_feedback("p_x", old=None, new=new, notes="immediate family")

    recent = store.recent_feedback()
    assert len(recent) == 1
    assert recent[0]["person_id"] == "p_x"
    assert recent[0]["new_tier"] == "core"
    assert recent[0]["notes"] == "immediate family"


def test_record_feedback_correction(store: ClassificationStore):
    old = _result("p_y", tier=Tier.ACTIVE, tags=[])
    new = _result("p_y", tier=Tier.CORE, tags=[{"tag": "close_friend", "confidence": 0.9}])
    store.record_feedback("p_y", old=old, new=new, notes="closer than I thought")

    recent = store.recent_feedback()
    assert len(recent) == 1
    entry = recent[0]
    assert entry["old_tier"] == "active"
    assert entry["new_tier"] == "core"
    assert len(entry["new_tags"]) == 1
    assert entry["new_tags"][0]["tag"] == "close_friend"


def test_recent_feedback_ordering(store: ClassificationStore):
    import time
    for i, pid in enumerate(["p_1", "p_2", "p_3"]):
        store.record_feedback(
            pid,
            old=None,
            new=_result(pid, tier=Tier.ACTIVE),
        )
        time.sleep(0.01)  # ensure distinct timestamps

    recent = store.recent_feedback()
    # Newest first
    person_ids = [r["person_id"] for r in recent]
    assert person_ids[0] == "p_3"
    assert person_ids[-1] == "p_1"


def test_recent_feedback_limit(store: ClassificationStore):
    for i in range(20):
        store.record_feedback(
            f"p_{i}",
            old=None,
            new=_result(f"p_{i}", tier=Tier.ACTIVE),
        )

    recent = store.recent_feedback(limit=5)
    assert len(recent) == 5


def test_feedback_for_person(store: ClassificationStore):
    store.record_feedback("p_a", old=None, new=_result("p_a", tier=Tier.CORE), notes="first")
    store.record_feedback("p_a", old=_result("p_a", tier=Tier.CORE), new=_result("p_a", tier=Tier.ACTIVE), notes="second")
    store.record_feedback("p_b", old=None, new=_result("p_b", tier=Tier.ACTIVE))

    a_feedback = store.feedback_for_person("p_a")
    assert len(a_feedback) == 2
    # Newest first
    assert a_feedback[0]["notes"] == "second"

    b_feedback = store.feedback_for_person("p_b")
    assert len(b_feedback) == 1


def test_feedback_empty_for_unknown_person(store: ClassificationStore):
    assert store.feedback_for_person("p_ghost") == []


# ── Edge cases ────────────────────────────────────────────────────────


def test_classification_with_reasoning_unicode(store: ClassificationStore):
    result = _result(
        "p_unicode",
        tier=Tier.ACTIVE,
        reasoning="multi-byte — ñöò test",
    )
    store.save(result)
    loaded = store.load("p_unicode")
    assert loaded is not None
    assert "ñöò" in loaded.reasoning


def test_classification_empty_tags_roundtrip(store: ClassificationStore):
    store.save(_result("p_plain", tier=Tier.ACTIVE, tags=[]))
    loaded = store.load("p_plain")
    assert loaded is not None
    assert loaded.context_tags == []


def test_missing_db_graceful(tmp_path: Path):
    store = ClassificationStore(tmp_path / "does_not_exist.db")
    # Don't init — table doesn't exist
    assert store.load("p_any") is None
    assert store.load_many(["p_any"]) == []
    assert store.tier_distribution() == {}
    assert store.recent_feedback() == []


def test_feedback_preserves_new_tags_on_correction(store: ClassificationStore):
    """When operator corrects, the new tags must survive the JSON roundtrip."""
    new = _result(
        "p_cx",
        tier=Tier.CORE,
        tags=[
            {"tag": "family_nuclear", "confidence": 0.95},
            {"tag": "childhood", "confidence": 0.9},
        ],
    )
    store.record_feedback("p_cx", old=None, new=new, notes="my sibling")

    recent = store.recent_feedback()
    assert len(recent) == 1
    tags = recent[0]["new_tags"]
    assert len(tags) == 2
    tag_names = {t["tag"] for t in tags}
    assert tag_names == {"family_nuclear", "childhood"}
