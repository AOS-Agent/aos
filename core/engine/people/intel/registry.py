"""Adapter Registry — discovers and manages signal source adapters.

The registry holds all known adapters and reports which are available,
what signal types are covered, and what's missing. It does NOT run
extraction — that's the extractor's job. The registry just manages the
catalog.

Discovery is filesystem-driven: every module in the `sources/` package
(except `base`) is imported, and any SignalAdapter subclass found is
registered. No hardcoded lists.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Type

from .sources.base import SignalAdapter
from .types import SignalType

log = logging.getLogger(__name__)


class AdapterRegistry:
    """Registry of signal source adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, Type[SignalAdapter]] = {}
        self._availability_cache: dict[str, bool] = {}

    # ── Registration ──

    def register(self, adapter_cls: Type[SignalAdapter]) -> None:
        """Register an adapter class by its `name` attribute."""
        if not adapter_cls.name:
            log.warning("Adapter %s has no name attribute, skipping", adapter_cls)
            return
        self._adapters[adapter_cls.name] = adapter_cls
        # Invalidate cache entry if this replaces an existing adapter
        self._availability_cache.pop(adapter_cls.name, None)

    def discover(self) -> None:
        """Auto-discover adapters from the sources package.

        Imports all modules in `core.engine.people.intel.sources` and
        registers any SignalAdapter subclasses found. Import errors are
        logged but do not abort discovery — one broken adapter doesn't
        break the rest.
        """
        from . import sources as sources_pkg

        for _importer, modname, _ispkg in pkgutil.iter_modules(sources_pkg.__path__):
            if modname == "base":
                continue
            try:
                mod = importlib.import_module(f".{modname}", sources_pkg.__name__)
            except Exception as e:
                log.warning("Failed to import adapter module %s: %s", modname, e)
                continue

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, SignalAdapter)
                    and attr is not SignalAdapter
                    and attr.name
                ):
                    self.register(attr)

    # ── Queries ──

    def all_adapters(self) -> list[str]:
        """Return names of all registered adapters (available or not)."""
        return sorted(self._adapters.keys())

    def available(self) -> list[str]:
        """Return names of adapters that are available on this machine."""
        return sorted(
            name for name in self._adapters if self._check_availability(name)
        )

    def get(self, name: str) -> SignalAdapter | None:
        """Instantiate and return an adapter by name."""
        cls = self._adapters.get(name)
        return cls() if cls else None

    def capability(self, name: str) -> dict | None:
        """Return static capability descriptor for an adapter."""
        cls = self._adapters.get(name)
        if not cls:
            return None
        return {
            "name": cls.name,
            "display_name": cls.display_name or cls.name,
            "platform": cls.platform,
            "signal_types": [s.value for s in cls.signal_types],
            "description": cls.description,
            "requires": list(cls.requires),
        }

    def coverage_report(self) -> dict:
        """Report what signal types are covered by available adapters."""
        all_names = self.all_adapters()
        available_names = set(self.available())

        covered_types: set[SignalType] = set()
        available_details: list[dict] = []
        unavailable_details: list[dict] = []

        for name in all_names:
            cls = self._adapters[name]
            is_avail = name in available_names
            detail = {
                "name": cls.name,
                "display_name": cls.display_name or cls.name,
                "platform": cls.platform,
                "signal_types": [s.value for s in cls.signal_types],
                "description": cls.description,
                "available": is_avail,
            }
            if is_avail:
                available_details.append(detail)
                covered_types.update(cls.signal_types)
            else:
                unavailable_details.append(detail)

        missing_types = set(SignalType) - covered_types

        return {
            "available_count": len(available_names),
            "total_count": len(all_names),
            "coverage_pct": (
                len(available_names) / len(all_names) if all_names else 0
            ),
            "signal_types_covered": sorted(t.value for t in covered_types),
            "signal_types_missing": sorted(t.value for t in missing_types),
            "available": available_details,
            "unavailable": unavailable_details,
        }

    # ── Internal ──

    def _check_availability(self, name: str) -> bool:
        """Check if an adapter is available. Cached until invalidate_cache()."""
        if name in self._availability_cache:
            return self._availability_cache[name]
        cls = self._adapters.get(name)
        if not cls:
            self._availability_cache[name] = False
            return False
        try:
            result = cls().is_available()
        except Exception as e:
            log.warning("Adapter %s.is_available() raised: %s", name, e)
            result = False
        self._availability_cache[name] = bool(result)
        return self._availability_cache[name]

    def invalidate_cache(self) -> None:
        """Clear availability cache (e.g., after new source connected)."""
        self._availability_cache.clear()
