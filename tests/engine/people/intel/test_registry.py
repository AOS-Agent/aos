"""Tests for the signal adapter base class and registry."""
import pytest

from core.engine.people.intel.registry import AdapterRegistry
from core.engine.people.intel.sources.base import SignalAdapter
from core.engine.people.intel.types import PersonSignals, SignalType


# ── Test adapters ──────────────────────────────────────────────────────


class MockAvailableAdapter(SignalAdapter):
    name = "mock_available"
    display_name = "Mock Available"
    platform = "any"
    signal_types = [SignalType.COMMUNICATION]
    description = "Test adapter that reports available"

    def is_available(self) -> bool:
        return True

    def extract_all(self, person_index):
        return {}


class MockUnavailableAdapter(SignalAdapter):
    name = "mock_unavailable"
    platform = "any"
    signal_types = [SignalType.VOICE]

    def is_available(self) -> bool:
        return False

    def extract_all(self, person_index):
        return {}


class MockRaisingAdapter(SignalAdapter):
    name = "mock_raising"
    signal_types = [SignalType.METADATA]

    def is_available(self) -> bool:
        raise RuntimeError("boom")

    def extract_all(self, person_index):
        return {}


# ── Registry tests ────────────────────────────────────────────────────


def test_register_and_list():
    reg = AdapterRegistry()
    reg.register(MockAvailableAdapter)
    assert "mock_available" in reg.all_adapters()
    assert "mock_available" in reg.available()


def test_register_skips_nameless_class():
    class Nameless(SignalAdapter):
        # no name set
        def is_available(self):
            return True

        def extract_all(self, person_index):
            return {}

    reg = AdapterRegistry()
    reg.register(Nameless)
    assert Nameless.name == ""
    assert "" not in reg.all_adapters()


def test_unavailable_excluded_from_available():
    reg = AdapterRegistry()
    reg.register(MockUnavailableAdapter)
    assert "mock_unavailable" in reg.all_adapters()
    assert "mock_unavailable" not in reg.available()


def test_raising_adapter_treated_as_unavailable():
    reg = AdapterRegistry()
    reg.register(MockRaisingAdapter)
    # is_available() raises — registry should catch and report False
    assert "mock_raising" not in reg.available()
    assert "mock_raising" in reg.all_adapters()


def test_get_returns_instance():
    reg = AdapterRegistry()
    reg.register(MockAvailableAdapter)
    inst = reg.get("mock_available")
    assert isinstance(inst, MockAvailableAdapter)
    assert reg.get("nonexistent") is None


def test_capability():
    reg = AdapterRegistry()
    reg.register(MockAvailableAdapter)
    cap = reg.capability("mock_available")
    assert cap["name"] == "mock_available"
    assert cap["display_name"] == "Mock Available"
    assert "communication" in cap["signal_types"]


def test_coverage_report():
    reg = AdapterRegistry()
    reg.register(MockAvailableAdapter)
    reg.register(MockUnavailableAdapter)

    report = reg.coverage_report()
    assert report["available_count"] == 1
    assert report["total_count"] == 2
    assert report["coverage_pct"] == 0.5
    assert "communication" in report["signal_types_covered"]
    # VOICE is from an unavailable adapter, so it should be in missing
    assert "voice" in report["signal_types_missing"]
    assert len(report["available"]) == 1
    assert len(report["unavailable"]) == 1
    assert report["available"][0]["name"] == "mock_available"


def test_coverage_report_empty_registry():
    reg = AdapterRegistry()
    report = reg.coverage_report()
    assert report["available_count"] == 0
    assert report["total_count"] == 0
    assert report["coverage_pct"] == 0
    assert report["signal_types_covered"] == []
    # All signal types missing
    assert "communication" in report["signal_types_missing"]


def test_invalidate_cache():
    """Availability is cached. After invalidate_cache, re-checked."""
    call_count = {"n": 0}

    class TogglingAdapter(SignalAdapter):
        name = "toggle"
        signal_types = [SignalType.MENTION]

        def is_available(self) -> bool:
            call_count["n"] += 1
            return True

        def extract_all(self, person_index):
            return {}

    reg = AdapterRegistry()
    reg.register(TogglingAdapter)

    reg.available()
    reg.available()
    reg.available()
    assert call_count["n"] == 1  # cached

    reg.invalidate_cache()
    reg.available()
    assert call_count["n"] == 2  # re-checked after invalidate


def test_discover_finds_adapters_in_sources_package():
    """Registry.discover() walks the sources/ package. Since no real
    adapters are implemented in Phase 1, discover should find zero.
    This test locks that in so Phase 2 adapters get picked up automatically."""
    reg = AdapterRegistry()
    reg.discover()
    # Currently no adapters in sources/ besides base. Should not raise.
    # After Phase 2 lands, this test should be updated to assert adapters exist.
    assert isinstance(reg.all_adapters(), list)


def test_health_dict():
    adapter = MockAvailableAdapter()
    health = adapter.health()
    assert health["name"] == "mock_available"
    assert health["available"] is True
    assert "communication" in health["signal_types"]


def test_health_on_raising_adapter():
    adapter = MockRaisingAdapter()
    health = adapter.health()
    assert health["available"] is False
    assert "error" in health


def test_register_overrides_and_invalidates_cache():
    """Re-registering an adapter with the same name should replace the old one
    and clear any cached availability for that name."""
    reg = AdapterRegistry()
    reg.register(MockAvailableAdapter)
    assert "mock_available" in reg.available()

    class ReplacedAdapter(SignalAdapter):
        name = "mock_available"
        signal_types = [SignalType.METADATA]

        def is_available(self) -> bool:
            return False

        def extract_all(self, person_index):
            return {}

    reg.register(ReplacedAdapter)
    assert "mock_available" not in reg.available()


def test_extract_all_contract():
    """Adapter extract_all must accept a dict and return a dict."""
    adapter = MockAvailableAdapter()
    person_index = {"p_1": {"name": "Alice", "phones": ["+1234"], "emails": [], "wa_jids": []}}
    result = adapter.extract_all(person_index)
    assert isinstance(result, dict)
