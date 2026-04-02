"""Qareen Agents — Type Definitions.

Types for agent workers: definitions loaded from .md files, task
assignments, memory entries, and the abstract worker interface.

Agents in AOS are specialist workers dispatched by the Chief. Each
agent has a domain, a set of tools and skills, and a trust level
governing what it can do autonomously.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..ontology.types import TrustLevel

# ---------------------------------------------------------------------------
# Agent definition (loaded from .md files)
# ---------------------------------------------------------------------------

@dataclass
class AgentDefinition:
    """Definition of an agent, parsed from its .md file.

    Attributes:
        id: Unique agent identifier (e.g. "marketing", "steward").
        name: Human-readable agent name.
        domain: The agent's area of expertise (e.g. "marketing",
            "system health", "analysis").
        description: What this agent does, in natural language.
        model: The LLM model this agent uses (e.g. "sonnet", "opus").
        tools: List of tool names this agent has access to.
        skills: List of skill names this agent can invoke.
        default_trust: The base trust level for this agent. Individual
            action types may have different trust via TrustEntry.
        schedule: Scheduled tasks as a dict of trigger → description
            (e.g. {"daily": "check campaign performance"}).
        is_system: True for core agents (chief, steward, advisor) that
            cannot be deactivated.
        source_path: Filesystem path to the agent's .md definition file,
            or None if defined programmatically.
    """

    id: str
    name: str
    domain: str = ""
    description: str = ""
    model: str = "sonnet"
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    default_trust: TrustLevel = TrustLevel.SURFACE
    schedule: dict[str, str] = field(default_factory=dict)
    is_system: bool = False
    source_path: str | None = None


# ---------------------------------------------------------------------------
# Agent task (work unit dispatched to an agent)
# ---------------------------------------------------------------------------

@dataclass
class AgentTask:
    """A work unit assigned to an agent for execution.

    Attributes:
        id: Unique task identifier (UUID).
        agent_id: The agent this task is assigned to.
        task_type: The kind of work — e.g. "analyze", "draft",
            "research", "execute_pipeline".
        params: Parameters for the task, specific to the task_type.
        status: Lifecycle state — queued, running, completed, or failed.
        created: When the task was created.
        started: When the agent began working on this task, or None.
        completed: When the agent finished, or None if still in progress.
        result: The task's output, set on completion. Type depends
            on the task_type.
        error: Error message if the task failed, or None on success.
    """

    id: str
    agent_id: str
    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"  # queued | running | completed | failed
    created: datetime = field(default_factory=datetime.now)
    started: datetime | None = None
    completed: datetime | None = None
    result: Any = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Agent memory (learned patterns and preferences)
# ---------------------------------------------------------------------------

@dataclass
class AgentMemoryEntry:
    """A piece of knowledge an agent has learned over time.

    Agent memory is key-value pairs scoped to an agent. It records
    patterns, preferences, and context the agent picks up across
    sessions to improve its future performance.

    Attributes:
        agent_id: The agent that owns this memory entry.
        key: A descriptive key for what was learned (e.g.
            "nuchay_pricing_pattern", "operator_prefers_bullet_points").
        value: The learned content, in natural language or structured text.
        learned_at: When this memory entry was created or last updated.
        source: What session, interaction, or event taught the agent
            this knowledge (e.g. "session:abc123", "feedback:2025-03-30").
    """

    agent_id: str
    key: str
    value: str
    learned_at: datetime = field(default_factory=datetime.now)
    source: str = ""


# ---------------------------------------------------------------------------
# Agent worker (abstract base for execution)
# ---------------------------------------------------------------------------

class AgentWorker(abc.ABC):
    """Abstract base class for agent task execution.

    Concrete workers implement domain-specific task execution and
    reporting. The registry dispatches AgentTasks to the appropriate
    worker based on the agent's definition.
    """

    @abc.abstractmethod
    def execute(self, task: AgentTask) -> Any:
        """Execute an agent task and return the result.

        Implementations should:
          1. Validate the task params for their domain
          2. Perform the work (LLM calls, data lookups, etc.)
          3. Return the result (type depends on task_type)

        The caller handles status transitions and error capture.

        Args:
            task: The agent task to execute. The task's params dict
                contains task-type-specific parameters.

        Returns:
            The task result. Type and structure depend on the
            task_type.

        Raises:
            Any exception — the caller catches and records the error.
        """
        ...

    @abc.abstractmethod
    def report(self, task: AgentTask, result: Any) -> None:
        """Report the result of a completed task.

        Called after execute() succeeds. Implementations should
        emit events, update state, or notify the operator as
        appropriate for their domain.

        Args:
            task: The completed agent task.
            result: The result returned by execute().
        """
        ...
