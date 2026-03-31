"""Qareen API — Channel management routes.

List communication channels and their status.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ..ontology.types import ChannelType
from .schemas import ChannelListResponse, ChannelStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

AOS_DATA = Path.home() / ".aos"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning empty dict on error."""
    try:
        import yaml
        if not path.exists():
            return {}
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _channel_type_from_str(s: str) -> ChannelType:
    """Convert a string to a ChannelType enum, defaulting to TELEGRAM."""
    try:
        return ChannelType(s.lower())
    except ValueError:
        return ChannelType.TELEGRAM


@router.get("", response_model=ChannelListResponse)
async def list_channels(request: Request) -> ChannelListResponse:
    """List all communication channels with their current status."""
    channels: list[ChannelStatusResponse] = []

    # Read from channels config or accounts config
    config_path = AOS_DATA / "config" / "channels.yaml"
    data = _load_yaml(config_path)

    if not data:
        # Fallback: discover from accounts.yaml
        accounts_path = AOS_DATA / "config" / "accounts.yaml"
        data = _load_yaml(accounts_path)

    raw_channels = data.get("channels", [])
    if isinstance(raw_channels, dict):
        for ch_id, cfg in raw_channels.items():
            if isinstance(cfg, dict):
                channels.append(ChannelStatusResponse(
                    id=ch_id,
                    channel_type=_channel_type_from_str(cfg.get("type", "telegram")),
                    name=cfg.get("name", ch_id),
                    is_active=cfg.get("enabled", cfg.get("is_active", True)),
                    is_healthy=cfg.get("is_healthy", True),
                    messages_today=cfg.get("messages_today", 0),
                ))
    elif isinstance(raw_channels, list):
        for ch in raw_channels:
            if isinstance(ch, dict):
                channels.append(ChannelStatusResponse(
                    id=ch.get("id", ""),
                    channel_type=_channel_type_from_str(ch.get("type", "telegram")),
                    name=ch.get("name", ch.get("id", "")),
                    is_active=ch.get("enabled", ch.get("is_active", True)),
                    is_healthy=ch.get("is_healthy", True),
                    messages_today=ch.get("messages_today", 0),
                ))

    # If no channels found in config, add known channels from the system
    if not channels:
        known_channels = [
            ("telegram", ChannelType.TELEGRAM, "Telegram"),
            ("whatsapp", ChannelType.WHATSAPP, "WhatsApp"),
        ]
        for ch_id, ch_type, ch_name in known_channels:
            channels.append(ChannelStatusResponse(
                id=ch_id,
                channel_type=ch_type,
                name=ch_name,
                is_active=True,
                is_healthy=True,
            ))

    active = sum(1 for c in channels if c.is_active)
    healthy = sum(1 for c in channels if c.is_healthy)

    return ChannelListResponse(
        channels=channels,
        total=len(channels),
        active_count=active,
        healthy_count=healthy,
    )
