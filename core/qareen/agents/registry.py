"""Qareen Agent Registry — Interface.

The registry discovers agent definitions from .md files, manages
activation/deactivation, and provides lookups. It does NOT execute
agent tasks — that's the worker's job.

Directory convention:
  - Active agents: ~/aos/core/agents/ (chief.md, steward.md, advisor.md, ...)
  - Catalog agents: ~/aos/core/agents/catalog/ (available but not active)

Discovering scans .md files with YAML frontmatter containing agent
metadata (id, name, domain, tools, skills, trust, schedule).
"""

from __future__ import annotations

from .types import AgentDefinition


class AgentRegistry:
    """Manages agent definitions: discovery, activation, and lookup.

    The registry maintains two pools:
      - Active: agents currently available for task dispatch.
      - Catalog: agents available for activation but not currently active.

    System agents (chief, steward, advisor) are always active and
    cannot be deactivated.
    """

    def __init__(self) -> None:
        """Initialize an empty agent registry."""
        self._active: dict[str, AgentDefinition] = {}
        self._catalog: dict[str, AgentDefinition] = {}

    def discover(self, agents_dir: str) -> None:
        """Scan a directory for agent .md files and load definitions.

        Parses each .md file's YAML frontmatter to build an
        AgentDefinition. Files without valid frontmatter are skipped
        with a warning.

        System agents (is_system=True in frontmatter) are added to
        the active pool. All others go to the catalog.

        Args:
            agents_dir: Absolute path to the directory containing
                agent .md definition files.

        Raises:
            FileNotFoundError: If the directory does not exist.
        """
        raise NotImplementedError

    def get(self, agent_id: str) -> AgentDefinition | None:
        """Get an agent definition by id.

        Searches active agents first, then the catalog.

        Args:
            agent_id: The unique agent identifier.

        Returns:
            The AgentDefinition if found in either pool, or None.
        """
        raise NotImplementedError

    def list_active(self) -> list[AgentDefinition]:
        """List all currently active agent definitions.

        Returns:
            A list of AgentDefinition instances for all active agents,
            sorted by id.
        """
        raise NotImplementedError

    def list_catalog(self) -> list[AgentDefinition]:
        """List all catalog (available but not active) agent definitions.

        Returns:
            A list of AgentDefinition instances for all catalog agents,
            sorted by id.
        """
        raise NotImplementedError

    def activate(self, agent_id: str) -> bool:
        """Move an agent from catalog to active.

        If the agent is already active, returns True without changes.
        If the agent is not found in either pool, returns False.

        Args:
            agent_id: The unique agent identifier to activate.

        Returns:
            True if the agent is now active (including already-active),
            False if the agent was not found.
        """
        raise NotImplementedError

    def deactivate(self, agent_id: str) -> bool:
        """Move an agent from active to catalog.

        System agents (is_system=True) cannot be deactivated — this
        method returns False for them.

        If the agent is already in the catalog, returns True without
        changes. If the agent is not found, returns False.

        Args:
            agent_id: The unique agent identifier to deactivate.

        Returns:
            True if the agent is now in the catalog (including
            already-cataloged), False if not found or is a system agent.
        """
        raise NotImplementedError
