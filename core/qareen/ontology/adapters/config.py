"""Config Adapter — Agents, Channels, Integrations (read-only).

Discovers AGENT, CHANNEL, and INTEGRATION objects from the filesystem.
No database. Reads .md files from ~/.claude/agents/ for agents,
and registry.yaml for integrations. Channels are communication-category
integrations. All mutation methods raise NotImplementedError.

Links are stored in qareen.db (shared with other adapters).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from ..types import (
    Agent,
    Channel,
    ChannelType,
    Integration,
    Link,
    LinkType,
    ObjectType,
    TrustLevel,
)
from .base import Adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file.

    Returns an empty dict if the file has no frontmatter or cannot be parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    if not text.startswith("---"):
        return {}

    end = text.find("\n---", 3)
    if end == -1:
        return {}

    fm_text = text[3:end].strip()
    try:
        data = yaml.safe_load(fm_text)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _parse_body(path: Path) -> str:
    """Extract the body (after frontmatter) from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    if not text.startswith("---"):
        return text

    end = text.find("\n---", 3)
    if end == -1:
        return text

    return text[end + 4:].strip()


def _channel_type_from_key(key: str) -> ChannelType:
    """Map an integration key to a ChannelType enum value."""
    mapping = {
        "telegram": ChannelType.TELEGRAM,
        "whatsapp": ChannelType.WHATSAPP,
        "email": ChannelType.EMAIL,
        "slack": ChannelType.SLACK,
        "sms": ChannelType.SMS,
        "messages": ChannelType.SMS,  # iMessage -> SMS type
        "mail": ChannelType.EMAIL,
    }
    # Check the last segment of dotted IDs like "builtin.telegram"
    short_key = key.rsplit(".", 1)[-1] if "." in key else key
    return mapping.get(short_key, ChannelType.EMAIL)


def _trust_from_str(val: str | None) -> TrustLevel:
    """Convert a string trust level to enum, defaulting to SURFACE."""
    if not val:
        return TrustLevel.SURFACE
    try:
        return TrustLevel[val.upper()]
    except (KeyError, AttributeError):
        return TrustLevel.SURFACE


def _tools_list(val: Any) -> list[str]:
    """Normalize the tools field from frontmatter into a list of strings."""
    if val is None:
        return []
    if isinstance(val, str):
        return ["*"] if val == "*" else [val]
    if isinstance(val, list):
        return [str(t) for t in val]
    return []


# ---------------------------------------------------------------------------
# ConfigAdapter
# ---------------------------------------------------------------------------

class ConfigAdapter(Adapter):
    """Read-only adapter for system configuration objects (agents, channels, integrations).

    Discovers objects from the filesystem:
    - AGENT: reads .md files from ~/.claude/agents/
    - CHANNEL: derived from integrations with category=communication
    - INTEGRATION: reads from registry.yaml
    """

    def __init__(self, agents_dir: Path, integrations_registry: Path):
        self._agents_dir = agents_dir
        self._registry_path = integrations_registry
        self._qareen_db_path = Path.home() / ".aos" / "data" / "qareen.db"

    # ── Cached registry parsing ─────────────────────────────

    @cached_property
    def _registry(self) -> dict[str, dict[str, Any]]:
        """Parse registry.yaml into a flat dict of integration_id -> info.

        Keys are dotted: "apple_native.calendar", "builtin.telegram", etc.
        """
        try:
            text = self._registry_path.read_text(encoding="utf-8")
            raw = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            return {}

        if not isinstance(raw, dict):
            return {}

        flat: dict[str, dict[str, Any]] = {}
        for tier_key, entries in raw.items():
            if not isinstance(entries, dict):
                continue
            for entry_key, entry_data in entries.items():
                if not isinstance(entry_data, dict):
                    continue
                integration_id = f"{tier_key}.{entry_key}"
                flat[integration_id] = {
                    "id": integration_id,
                    "short_key": entry_key,
                    "tier_key": tier_key,
                    **entry_data,
                }
        return flat

    # ── Adapter interface ───────────────────────────────────

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.AGENT  # primary type; also handles CHANNEL, INTEGRATION

    def get(self, object_id: str) -> Agent | Channel | Integration | None:
        """Get a single object by id.

        Tries agent first (e.g. "chief"), then integration (e.g. "builtin.telegram"),
        then channel (communication integrations, by short key like "telegram").
        """
        # Try as agent name
        agent = self._get_agent(object_id)
        if agent is not None:
            return agent

        # Try as full integration ID (e.g. "builtin.telegram")
        integration = self._get_integration(object_id)
        if integration is not None:
            return integration

        # Try as channel (short key match against communication integrations)
        channel = self._get_channel(object_id)
        if channel is not None:
            return channel

        return None

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List objects, dispatched by _type filter.

        Supported _type values: "agent", "channel", "integration".
        """
        filters = filters or {}
        obj_type = filters.pop("_type", "agent")

        if obj_type == "agent":
            return self._list_agents(filters, limit, offset)
        elif obj_type == "channel":
            return self._list_channels(filters, limit, offset)
        elif obj_type == "integration":
            return self._list_integrations(filters, limit, offset)
        else:
            return self._list_agents(filters, limit, offset)

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        """Count objects matching filters."""
        filters = filters or {}
        obj_type = filters.pop("_type", "agent")

        if obj_type == "agent":
            return len(self._list_agents(filters, limit=10000, offset=0))
        elif obj_type == "channel":
            return len(self._list_channels(filters, limit=10000, offset=0))
        elif obj_type == "integration":
            return len(self._list_integrations(filters, limit=10000, offset=0))
        else:
            return 0

    def create(self, obj: Any) -> Any:
        """Not supported — read-only adapter."""
        raise NotImplementedError("ConfigAdapter is read-only")

    def update(self, object_id: str, fields: dict[str, Any]) -> Any | None:
        """Not supported — read-only adapter."""
        raise NotImplementedError("ConfigAdapter is read-only")

    def delete(self, object_id: str) -> bool:
        """Not supported — read-only adapter."""
        raise NotImplementedError("ConfigAdapter is read-only")

    # ── Links (stored in qareen.db) ─────────────────────────

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get ids of linked objects from qareen.db."""
        try:
            conn = sqlite3.connect(str(self._qareen_db_path))
            conn.row_factory = sqlite3.Row
            if link_type:
                rows = conn.execute(
                    "SELECT to_id FROM links "
                    "WHERE from_id = ? AND to_type = ? AND link_type = ? "
                    "LIMIT ?",
                    (obj_id, target_type.value, link_type.value, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT to_id FROM links "
                    "WHERE from_id = ? AND to_type = ? "
                    "LIMIT ?",
                    (obj_id, target_type.value, limit),
                ).fetchall()
            conn.close()
            return [r["to_id"] for r in rows]
        except (sqlite3.OperationalError, OSError):
            return []

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Create a link between this object and another in qareen.db."""
        link_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # Detect source type from the object_id
        source_type = self._detect_source_type(source_id)

        conn = sqlite3.connect(str(self._qareen_db_path))
        conn.execute(
            "INSERT OR REPLACE INTO links "
            "(id, link_type, from_type, from_id, to_type, to_id, "
            " direction, properties, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'directed', ?, ?, 'config_adapter')",
            (
                link_id,
                link_type.value,
                source_type.value,
                source_id,
                target_type.value,
                target_id,
                self._json_dumps(metadata),
                now,
            ),
        )
        conn.commit()
        conn.close()

        return Link(
            link_type=link_type,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
        )

    # ── Search ──────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[Any]:
        """Case-insensitive search across agents and integrations."""
        results: list[Any] = []
        q = query.lower()

        # Search agents
        for agent in self._list_agents({}, limit=10000, offset=0):
            if (
                q in agent.id.lower()
                or q in agent.name.lower()
                or q in agent.description.lower()
                or q in agent.domain.lower()
            ):
                results.append(agent)

        # Search integrations
        for integration in self._list_integrations({}, limit=10000, offset=0):
            if (
                q in integration.id.lower()
                or q in integration.name.lower()
                or q in integration.category.lower()
                or any(q in cap.lower() for cap in integration.capabilities)
            ):
                results.append(integration)

        return results[:limit]

    # ── Context card (not supported) ────────────────────────

    def close(self) -> None:
        """No-op — no persistent connections to close."""
        pass

    # ── Internal: Agent operations ──────────────────────────

    def _get_agent(self, agent_id: str) -> Agent | None:
        """Load a single agent by name from its .md file."""
        if not self._agents_dir.is_dir():
            return None

        # Try exact filename match
        path = self._agents_dir / f"{agent_id}.md"
        if not path.is_file():
            # Try matching by frontmatter name
            for p in self._agents_dir.glob("*.md"):
                fm = _parse_frontmatter(p)
                if fm.get("name") == agent_id:
                    path = p
                    break
            else:
                return None

        return self._path_to_agent(path)

    def _path_to_agent(self, path: Path) -> Agent | None:
        """Parse an agent .md file into an Agent dataclass."""
        fm = _parse_frontmatter(path)
        if not fm:
            return None

        agent_id = fm.get("name", path.stem)
        tools = _tools_list(fm.get("tools"))
        skills = fm.get("skills", [])
        if isinstance(skills, str):
            skills = [skills]

        # Determine if system agent (chief, steward, advisor)
        system_agents = {"chief", "steward", "advisor"}
        is_system = agent_id in system_agents

        return Agent(
            id=agent_id,
            name=fm.get("name", path.stem),
            domain=fm.get("domain", fm.get("scope", "")),
            description=fm.get("description", ""),
            model=fm.get("model", "sonnet"),
            tools=tools,
            skills=skills if isinstance(skills, list) else [],
            default_trust=_trust_from_str(fm.get("trust")),
            schedule=fm.get("schedule", {}),
            is_system=is_system,
            is_active=fm.get("active", True),
            source_path=str(path),
        )

    def _list_agents(
        self, filters: dict[str, Any], limit: int, offset: int
    ) -> list[Agent]:
        """List all agents from the agents directory."""
        if not self._agents_dir.is_dir():
            return []

        agents: list[Agent] = []
        for path in sorted(self._agents_dir.glob("*.md")):
            # Skip help files
            if path.name.startswith("--"):
                continue

            agent = self._path_to_agent(path)
            if agent is None:
                continue

            # Apply filters
            if "name" in filters and filters["name"] != agent.name:
                continue
            if "model" in filters and filters["model"] != agent.model:
                continue
            if "status" in filters:
                want_active = filters["status"] == "active"
                if agent.is_active != want_active:
                    continue

            agents.append(agent)

        return agents[offset : offset + limit]

    # ── Internal: Integration operations ────────────────────

    def _get_integration(self, integration_id: str) -> Integration | None:
        """Get a single integration by its dotted ID."""
        entry = self._registry.get(integration_id)
        if entry is None:
            # Try matching by short key across all tiers
            for full_id, data in self._registry.items():
                if data.get("short_key") == integration_id:
                    entry = data
                    break
            if entry is None:
                return None

        return self._entry_to_integration(entry)

    def _entry_to_integration(self, entry: dict[str, Any]) -> Integration:
        """Convert a flattened registry entry to an Integration dataclass."""
        status = entry.get("status", "available")
        provides = entry.get("provides", [])
        if isinstance(provides, str):
            provides = [provides]

        return Integration(
            id=entry["id"],
            name=entry.get("name", entry.get("short_key", "")),
            category=entry.get("category", ""),
            is_active=status == "active",
            capabilities=provides,
            config={
                k: v
                for k, v in entry.items()
                if k not in ("id", "short_key", "tier_key", "name", "category",
                             "status", "provides", "tier", "description")
            },
        )

    def _list_integrations(
        self, filters: dict[str, Any], limit: int, offset: int
    ) -> list[Integration]:
        """List all integrations from the registry."""
        integrations: list[Integration] = []
        for entry in self._registry.values():
            integration = self._entry_to_integration(entry)

            # Apply filters
            if "tier" in filters:
                entry_tier = entry.get("tier")
                if entry_tier is not None and int(entry_tier) != int(filters["tier"]):
                    continue
            if "category" in filters and filters["category"] != integration.category:
                continue
            if "status" in filters:
                want_active = filters["status"] == "active"
                if integration.is_active != want_active:
                    continue

            integrations.append(integration)

        return integrations[offset : offset + limit]

    # ── Internal: Channel operations ────────────────────────

    def _get_channel(self, channel_id: str) -> Channel | None:
        """Get a channel by short key (communication integrations only)."""
        for entry in self._registry.values():
            if entry.get("category") != "communication":
                continue
            if entry.get("short_key") == channel_id or entry["id"] == channel_id:
                return self._entry_to_channel(entry)
        return None

    def _entry_to_channel(self, entry: dict[str, Any]) -> Channel:
        """Convert a communication integration entry to a Channel dataclass."""
        status = entry.get("status", "available")
        return Channel(
            id=entry.get("short_key", entry["id"]),
            channel_type=_channel_type_from_key(entry.get("short_key", "")),
            name=entry.get("name", ""),
            is_active=status == "active",
        )

    def _list_channels(
        self, filters: dict[str, Any], limit: int, offset: int
    ) -> list[Channel]:
        """List channels (communication-category integrations)."""
        channels: list[Channel] = []
        for entry in self._registry.values():
            if entry.get("category") != "communication":
                continue

            channel = self._entry_to_channel(entry)

            # Apply filters
            if "status" in filters:
                want_active = filters["status"] == "active"
                if channel.is_active != want_active:
                    continue

            channels.append(channel)

        return channels[offset : offset + limit]

    # ── Internal: Helpers ───────────────────────────────────

    def _detect_source_type(self, object_id: str) -> ObjectType:
        """Detect the ObjectType for a given ID."""
        # Check if it's an agent
        if self._get_agent(object_id) is not None:
            return ObjectType.AGENT
        # Check if it's a channel
        if self._get_channel(object_id) is not None:
            return ObjectType.CHANNEL
        # Check if it's an integration
        if self._get_integration(object_id) is not None:
            return ObjectType.INTEGRATION
        # Default to agent
        return ObjectType.AGENT

    @staticmethod
    def _json_dumps(val: dict | list | None) -> str | None:
        """Serialize to JSON string or return None."""
        if val is None:
            return None
        import json
        return json.dumps(val)
