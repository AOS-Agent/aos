"""Tests for the SignalExtractor orchestrator.

Unit tests use a fabricated tmp_path people.db and a fabricated adapter
registry so we don't touch any real operator data.
"""
import sqlite3
from pathlib import Path

import pytest

from core.engine.people.intel.extractor import RunReport, SignalExtractor
from core.engine.people.intel.sources.base import SignalAdapter
from core.engine.people.intel.types import (
    CommunicationSignal,
    MentionSignal,
    PersonSignals,
    SignalType,
    VoiceSignal,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _build_fake_people_db(path: Path) -> None:
    """Create a minimal people.db schema mirroring the real ontology."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE people (
            id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            is_archived INTEGER DEFAULT 0
        );
        CREATE TABLE person_identifiers (
            person_id TEXT NOT NULL,
            type TEXT NOT NULL,
            value TEXT NOT NULL,
            normalized TEXT,
            is_primary INTEGER DEFAULT 0,
            source TEXT,
            label TEXT,
            added_at INTEGER,
            PRIMARY KEY (person_id, type, value)
        );
        """
    )
    conn.executemany(
        "INSERT INTO people (id, canonical_name, display_name, first_name, last_name) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            # Well-formed spaced name
            ("p_clean", "Alex Kumar", "Alex Kumar", "Alex", "Kumar"),
            # CamelCase concatenated — normalizer should split
            ("p_camel", "FabricatedExample", None, None, None),
            # Compound record with slash
            ("p_compound", "NameOne/NameTwo", None, None, None),
            # Title + camelCase (normalizer strips "Dr")
            ("p_titled", "DrExamplePerson", None, None, None),
            # Archived — should NOT be indexed
            ("p_archived", "Archived Person", "Archived Person", "Archived", "Person"),
        ],
    )
    # Mark the archived row
    conn.execute("UPDATE people SET is_archived=1 WHERE id='p_archived'")
    conn.executemany(
        "INSERT INTO person_identifiers (person_id, type, value, normalized) "
        "VALUES (?, ?, ?, ?)",
        [
            ("p_clean", "phone", "+1-555-111-2222", "+15551112222"),
            ("p_clean", "email", "ALEX@example.com", "alex@example.com"),
            ("p_camel", "phone", "+1 555 333 4444", "+15553334444"),
            ("p_camel", "wa_jid", "15553334444@s.whatsapp.net", None),
            # Legacy alias type
            ("p_compound", "whatsapp", "15550000001@s.whatsapp.net", None),
            # Unknown type — should be ignored
            ("p_clean", "custom_thing", "ignored", None),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def fake_db(tmp_path: Path) -> Path:
    db = tmp_path / "people.db"
    _build_fake_people_db(db)
    return db


# ── Test adapters ─────────────────────────────────────────────────────


class _SuccessAdapter(SignalAdapter):
    name = "success_source"
    display_name = "Success"
    signal_types = [SignalType.COMMUNICATION]

    def is_available(self) -> bool:
        return True

    def extract_all(self, person_index):
        out = {}
        for pid, info in person_index.items():
            ps = PersonSignals(person_id=pid, person_name=info.get("name", ""))
            ps.source_coverage = [self.name]
            ps.communication.append(
                CommunicationSignal(source=self.name, channel="test", total_messages=7)
            )
            out[pid] = ps
        return out


class _FailingAdapter(SignalAdapter):
    name = "failing_source"
    signal_types = [SignalType.VOICE]

    def is_available(self) -> bool:
        return True

    def extract_all(self, person_index):
        raise RuntimeError("simulated extractor failure")


class _UnavailableAdapter(SignalAdapter):
    name = "unavailable_source"
    signal_types = [SignalType.MENTION]

    def is_available(self) -> bool:
        return False

    def extract_all(self, person_index):
        return {}


class _BadReturnAdapter(SignalAdapter):
    name = "bad_return_source"
    signal_types = [SignalType.VOICE]

    def is_available(self) -> bool:
        return True

    def extract_all(self, person_index):
        return "not a dict"  # wrong type


class _PartialAdapter(SignalAdapter):
    name = "partial_source"
    signal_types = [SignalType.MENTION]

    def is_available(self) -> bool:
        return True

    def extract_all(self, person_index):
        # Only return signals for one known person
        out = {}
        if "p_clean" in person_index:
            ps = PersonSignals(person_id="p_clean", person_name="Alex Kumar")
            ps.source_coverage = [self.name]
            ps.mentions.append(
                MentionSignal(source=self.name, total_mentions=3, work_task_mentions=3)
            )
            out["p_clean"] = ps
        return out


def _extractor_with(fake_db: Path, *adapter_classes) -> SignalExtractor:
    """Build a SignalExtractor with ONLY the given test adapters."""
    ex = SignalExtractor(db_path=fake_db)
    # Replace the auto-discovered registry with just our test adapters.
    ex.registry._adapters.clear()
    ex.registry.invalidate_cache()
    for cls in adapter_classes:
        ex.registry.register(cls)
    return ex


# ── build_person_index tests ──────────────────────────────────────────


def test_build_person_index_basic(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    # archived person excluded
    assert "p_archived" not in index
    assert "p_clean" in index
    assert "p_camel" in index
    assert "p_compound" in index
    assert "p_titled" in index


def test_index_has_normalized_name_variants(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    camel = index["p_camel"]
    # CamelCase should split via normalizer
    assert "fabricated example" in camel["variants"]
    # Primary should be the display form
    assert camel["name"] == "Fabricated Example"


def test_index_splits_compound_records(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    compound = index["p_compound"]
    assert "name one" in compound["variants"]
    assert "name two" in compound["variants"]


def test_index_strips_titles(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    titled = index["p_titled"]
    # "DrExamplePerson" → strip "Dr" → "example person"
    assert "example person" in titled["variants"]
    assert "dr example person" not in titled["variants"]


def test_index_attaches_phones(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    # Normalized phone should be attached
    assert "+15551112222" in index["p_clean"]["phones"]
    assert "+15553334444" in index["p_camel"]["phones"]


def test_index_attaches_emails_lowercased(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    # Email should be lowercased even if normalized column was different
    assert "alex@example.com" in index["p_clean"]["emails"]


def test_index_merges_whatsapp_legacy_alias_into_wa_jids(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    # p_compound has a "whatsapp" type identifier that should go into wa_jids
    assert len(index["p_compound"]["wa_jids"]) >= 1


def test_index_ignores_unknown_identifier_types(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index()
    # "custom_thing" type should not become a key
    assert "custom_things" not in index["p_clean"]
    # And the base buckets shouldn't contain it
    assert "ignored" not in index["p_clean"]["phones"]
    assert "ignored" not in index["p_clean"]["emails"]


def test_index_filter_by_person_ids(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index(person_ids=["p_clean", "p_camel"])
    assert set(index.keys()) == {"p_clean", "p_camel"}


def test_index_limit(fake_db):
    ex = _extractor_with(fake_db)
    index = ex.build_person_index(limit=2)
    assert len(index) == 2


def test_index_returns_empty_when_db_missing(tmp_path):
    ex = SignalExtractor(db_path=tmp_path / "nonexistent.db")
    # registry cleanup to isolate
    ex.registry._adapters.clear()
    index = ex.build_person_index()
    assert index == {}


# ── run() tests ───────────────────────────────────────────────────────


def test_run_successful_adapter(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    report = ex.run(dry_run=True)

    assert isinstance(report, RunReport)
    assert report.persons_indexed == 4  # 5 rows but 1 archived
    assert report.persons_extracted == 4
    assert "success_source" in report.sources_used
    assert report.per_source_persons["success_source"] == 4
    assert report.errors == []
    assert report.dry_run is True


def test_run_persists_when_not_dry_run(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    ex.run(dry_run=False)

    loaded = ex.get_person_signals("p_clean")
    assert loaded is not None
    assert any(c.total_messages == 7 for c in loaded.communication)


def test_run_dry_run_does_not_persist(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    ex.run(dry_run=True)

    loaded = ex.get_person_signals("p_clean")
    assert loaded is None


def test_run_captures_adapter_errors(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter, _FailingAdapter)
    report = ex.run(dry_run=True)

    # Success adapter should still run
    assert "success_source" in report.sources_used
    # Failing adapter should be in errors, not sources_used
    assert "failing_source" not in report.sources_used
    assert len(report.errors) == 1
    assert report.errors[0]["adapter"] == "failing_source"
    # Successful extraction still counted
    assert report.persons_extracted == 4


def test_run_handles_bad_return_type(fake_db):
    ex = _extractor_with(fake_db, _BadReturnAdapter)
    report = ex.run(dry_run=True)
    assert len(report.errors) == 1
    assert "non-dict" in report.errors[0]["error"]


def test_run_skips_unavailable_adapters(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter, _UnavailableAdapter)
    report = ex.run(dry_run=True)
    assert "success_source" in report.sources_used
    assert "unavailable_source" in report.sources_skipped
    assert "unavailable_source" not in report.sources_used


def test_run_filters_by_adapter_names(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter, _PartialAdapter)
    report = ex.run(dry_run=True, adapter_names=["success_source"])
    assert report.sources_used == ["success_source"]
    assert "partial_source" in report.sources_skipped


def test_run_filters_by_person_ids(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    report = ex.run(dry_run=True, person_ids=["p_clean"])
    assert report.persons_indexed == 1
    assert report.persons_extracted == 1


def test_run_limit_applied(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    report = ex.run(dry_run=True, limit=2)
    assert report.persons_indexed == 2
    assert report.persons_extracted == 2


def test_run_merges_signals_across_adapters(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter, _PartialAdapter)
    ex.run(dry_run=False)

    loaded = ex.get_person_signals("p_clean")
    assert loaded is not None
    # Both sources should have contributed
    assert len(loaded.communication) >= 1
    assert len(loaded.mentions) >= 1
    assert set(loaded.source_coverage) >= {"success_source", "partial_source"}


def test_run_with_no_adapters_returns_empty_report(fake_db):
    ex = _extractor_with(fake_db)  # no adapters registered
    report = ex.run(dry_run=True)
    assert report.persons_extracted == 0
    assert report.sources_used == []


def test_run_report_to_dict_serializable(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    report = ex.run(dry_run=True)
    d = report.to_dict()
    assert isinstance(d, dict)
    assert d["persons_indexed"] == 4
    assert "success_source" in d["sources_used"]
    # Must be JSON-safe
    import json
    json.dumps(d)


# ── Delegate method tests ────────────────────────────────────────────


def test_coverage_report_delegates_to_registry(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    cov = ex.coverage_report()
    assert cov["available_count"] == 1
    assert "success_source" in [a["name"] for a in cov["available"]]


def test_stats_after_run(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    ex.run(dry_run=False)
    stats = ex.stats()
    assert stats["total_rows"] == 4  # 4 non-archived persons × 1 source
    assert stats["distinct_persons"] == 4
    assert stats["by_source"]["success_source"] == 4


def test_get_person_signals_missing_returns_none(fake_db):
    ex = _extractor_with(fake_db, _SuccessAdapter)
    # Don't run yet
    assert ex.get_person_signals("p_clean") is None


# ── Real-registry smoke test (still uses fake people.db) ──────────────


def test_default_registry_autodiscovers_real_adapters(fake_db):
    """Without clearing the registry, the 9 real adapters should auto-register.
    We don't run them against the fake DB — just confirm discovery works."""
    ex = SignalExtractor(db_path=fake_db)
    registered = ex.registry.all_adapters()
    assert len(registered) >= 9
    assert "apple_messages" in registered
    assert "whatsapp" in registered
    assert "vault" in registered
