"""Communication channel registry.

Reads from the integrations registry (core/integrations/registry.yaml) to
discover which communication channels are active. No separate comms config —
the integrations registry is the single source of truth.

Usage:
    from core.comms.registry import get_active_channels, load_adapters

    # What comms integrations are available?
    channels = get_active_channels()

    # Load adapter instances for all active channels
    adapters = load_adapters()
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .channel import ChannelAdapter

# Path to integrations registry (relative to ~/aos/)
_AOS_ROOT = Path(os.environ.get("AOS_ROOT", Path.home() / "aos"))
_REGISTRY_PATH = _AOS_ROOT / "core" / "integrations" / "registry.yaml"

# Map integration IDs to their adapter module paths
# Only channels with implemented adapters are listed here.
# Adding a new adapter: implement the class, add the mapping.
_ADAPTER_MAP: dict[str, str] = {
    # integration_id (from registry.yaml) -> module path (relative to core.comms.channels)
    "whatsapp": "channels.whatsapp",
    "messages": "channels.imessage",    # "messages" is the registry ID for iMessage
    "telegram": "channels.telegram",
    "whatsapp_local": "channels.whatsapp_local",
    # Future:
    # "mail": "channels.email",
    # "email": "channels.email",
    # "calendar": "channels.calendar",
}

# Adapter class names by convention: {Name}Adapter
_ADAPTER_CLASSES: dict[str, str] = {
    "whatsapp": "WhatsAppAdapter",
    "messages": "iMessageAdapter",
    "telegram": "TelegramAdapter",
    "whatsapp_local": "WhatsAppLocalAdapter",
}


def _load_registry() -> dict:
    """Load the integrations registry YAML."""
    try:
        import yaml
    except ImportError:
        # Fallback: try to parse just what we need
        return {}

    if not _REGISTRY_PATH.exists():
        return {}

    with open(_REGISTRY_PATH) as f:
        return yaml.safe_load(f) or {}


def get_all_communication_integrations() -> list[dict]:
    """Return all integrations with category 'communication'.

    Scans all tiers (apple_native, builtin, catalog) for communication
    integrations. Returns a flat list of integration dicts with their
    tier and ID added.
    """
    registry = _load_registry()
    comms = []

    for tier_key in ("apple_native", "builtin", "catalog"):
        tier = registry.get(tier_key, {})
        if not isinstance(tier, dict):
            continue
        for integration_id, info in tier.items():
            if not isinstance(info, dict):
                continue
            if info.get("category") == "communication":
                entry = {**info, "id": integration_id, "tier": tier_key}
                comms.append(entry)

    return comms


def get_active_channels() -> list[dict]:
    """Return communication integrations that have adapters implemented.

    Filters to only channels where we have an adapter ready to load.
    """
    all_comms = get_all_communication_integrations()
    return [c for c in all_comms if c["id"] in _ADAPTER_MAP]


def load_adapter(channel_id: str) -> ChannelAdapter | None:
    """Load and instantiate a single channel adapter by integration ID.

    Returns None if the adapter module can't be loaded or the channel
    isn't in the adapter map.
    """
    if channel_id not in _ADAPTER_MAP:
        return None

    module_path = _ADAPTER_MAP[channel_id]
    class_name = _ADAPTER_CLASSES.get(channel_id)

    if not class_name:
        return None

    try:
        # Import relative to core.comms
        full_module = f"core.comms.{module_path}"
        module = importlib.import_module(full_module)
        adapter_class = getattr(module, class_name)
        return adapter_class()
    except (ImportError, AttributeError) as e:
        # Try relative import as fallback
        try:
            from . import channels
            module = importlib.import_module(f".{module_path}", package="core.comms")
            adapter_class = getattr(module, class_name)
            return adapter_class()
        except Exception:
            pass
        return None


def load_adapters() -> list[ChannelAdapter]:
    """Load adapter instances for all channels that have implementations.

    Only returns adapters that successfully instantiate. Channels that
    fail to load are skipped (logged but not fatal).
    """
    adapters = []
    for channel in get_active_channels():
        adapter = load_adapter(channel["id"])
        if adapter:
            adapters.append(adapter)
    return adapters
