"""Auto-discovery for eventd watchers and consumers.

Scans directories for subclasses and loads them automatically.
Drop a file → restart eventd → it's live. No registration needed.

Watchers:  core/services/eventd/watchers/*.py
Consumers: core/bus/consumers/*.py
"""

from __future__ import annotations

import importlib
import inspect
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Paths relative to AOS root
_AOS_ROOT = Path(__file__).parent.parent.parent.parent  # core/services/eventd -> aos/


def discover_watchers() -> list:
    """Find all BaseWatcher subclasses in the watchers directory."""
    from .watcher import BaseWatcher

    watchers_dir = Path(__file__).parent / "watchers"
    return _discover_subclasses(watchers_dir, BaseWatcher, "core.services.eventd.watchers")


def discover_consumers() -> list:
    """Find all EventConsumer subclasses in core/bus/consumers/."""
    # Add AOS root to path so imports work
    aos_root = str(_AOS_ROOT)
    if aos_root not in sys.path:
        sys.path.insert(0, aos_root)

    from core.bus.consumer import EventConsumer

    consumers_dir = _AOS_ROOT / "core" / "bus" / "consumers"
    return _discover_subclasses(consumers_dir, EventConsumer, "core.bus.consumers")


def _discover_subclasses(directory: Path, base_class: type, package_prefix: str) -> list:
    """Scan a directory for Python files and find subclasses of base_class."""
    if not directory.is_dir():
        log.warning("Discovery directory not found: %s", directory)
        return []

    # Ensure parent is importable
    aos_root = str(_AOS_ROOT)
    if aos_root not in sys.path:
        sys.path.insert(0, aos_root)

    instances = []

    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"{package_prefix}.{py_file.stem}"

        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            log.warning("Failed to import %s: %s", module_name, e)
            continue

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                inspect.isclass(attr)
                and issubclass(attr, base_class)
                and attr is not base_class
                and not inspect.isabstract(attr)
            ):
                try:
                    instance = attr()
                    instances.append(instance)
                    log.info("Discovered: %s from %s", attr.__name__, py_file.name)
                except Exception as e:
                    log.warning("Failed to instantiate %s: %s", attr.__name__, e)

    return instances
