"""Qareen Event Types.

Every mutation, signal, and state change in AOS is modeled as a typed event.
Events are immutable dataclasses emitted by actions and consumed by the bus.

All event types inherit from Event and carry:
  - event_type: dot-notation string (e.g. "task.created")
  - timestamp: when the event occurred
  - source: who emitted it (agent id, service name, or "operator")
  - payload: arbitrary dict for unstructured data

Type-specific fields carry the structured data relevant to each event kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..ontology.types import (
    ChannelType,
    ObjectType,
    TaskPriority,
)

# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """Base event. All events in the system inherit from this."""

    event_type: str
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "system"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to a plain dict for transport/storage."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Task events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskCreated(Event):
    """Emitted when a new task is created."""

    event_type: str = "task.created"
    task_id: str = ""
    title: str = ""
    project: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    assigned_to: str | None = None


@dataclass(frozen=True)
class TaskUpdated(Event):
    """Emitted when a task's fields are modified."""

    event_type: str = "task.updated"
    task_id: str = ""
    changed_fields: tuple[str, ...] = ()
    old_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCompleted(Event):
    """Emitted when a task transitions to done."""

    event_type: str = "task.completed"
    task_id: str = ""
    title: str = ""
    project: str | None = None
    completed_by: str | None = None
    duration_minutes: float | None = None


@dataclass(frozen=True)
class TaskDeleted(Event):
    """Emitted when a task is removed from the system."""

    event_type: str = "task.deleted"
    task_id: str = ""
    title: str = ""
    reason: str | None = None


# ---------------------------------------------------------------------------
# Message events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MessageReceived(Event):
    """Emitted when an inbound message arrives from any channel."""

    event_type: str = "message.received"
    message_id: str = ""
    channel: ChannelType | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    content_preview: str = ""
    has_attachment: bool = False
    thread_id: str | None = None


@dataclass(frozen=True)
class MessageSent(Event):
    """Emitted when an outbound message is dispatched."""

    event_type: str = "message.sent"
    message_id: str = ""
    channel: ChannelType | None = None
    recipient_id: str | None = None
    recipient_name: str | None = None
    content_preview: str = ""
    thread_id: str | None = None


# ---------------------------------------------------------------------------
# Context card events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardGenerated(Event):
    """Emitted when a context card is built or rebuilt."""

    event_type: str = "card.generated"
    entity_type: ObjectType | None = None
    entity_id: str = ""
    summary_preview: str = ""
    is_rebuild: bool = False


@dataclass(frozen=True)
class CardApproved(Event):
    """Emitted when an operator approves a generated card."""

    event_type: str = "card.approved"
    entity_type: ObjectType | None = None
    entity_id: str = ""
    approved_by: str = "operator"


@dataclass(frozen=True)
class CardDismissed(Event):
    """Emitted when an operator dismisses a generated card."""

    event_type: str = "card.dismissed"
    entity_type: ObjectType | None = None
    entity_id: str = ""
    dismissed_by: str = "operator"
    reason: str | None = None


# ---------------------------------------------------------------------------
# Person events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PersonMentioned(Event):
    """Emitted when a person is mentioned in a message, note, or task."""

    event_type: str = "person.mentioned"
    person_id: str = ""
    person_name: str = ""
    mentioned_in_type: str = ""  # "message", "note", "task"
    mentioned_in_id: str = ""
    context_snippet: str = ""


@dataclass(frozen=True)
class PersonUpdated(Event):
    """Emitted when a person's profile or relationship data changes."""

    event_type: str = "person.updated"
    person_id: str = ""
    person_name: str = ""
    changed_fields: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionStarted(Event):
    """Emitted when an agent session begins."""

    event_type: str = "session.started"
    session_id: str = ""
    agent_id: str | None = None
    project: str | None = None
    thread_id: str | None = None


@dataclass(frozen=True)
class SessionEnded(Event):
    """Emitted when an agent session concludes."""

    event_type: str = "session.ended"
    session_id: str = ""
    agent_id: str | None = None
    duration_seconds: float | None = None
    tasks_completed: int = 0
    tasks_created: int = 0
    outcome: str | None = None


# ---------------------------------------------------------------------------
# Service events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceHealthChanged(Event):
    """Emitted when a service's health status transitions."""

    event_type: str = "service.health_changed"
    service_name: str = ""
    previous_status: str = ""  # "healthy", "degraded", "down"
    new_status: str = ""
    error_message: str | None = None
    check_latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Pipeline events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PipelineStageCompleted(Event):
    """Emitted when a single stage of a pipeline finishes successfully."""

    event_type: str = "pipeline.stage_completed"
    pipeline_name: str = ""
    stage_name: str = ""
    stage_index: int = 0
    total_stages: int = 0
    entity_id: str | None = None
    duration_ms: float | None = None


@dataclass(frozen=True)
class PipelineCompleted(Event):
    """Emitted when an entire pipeline run finishes successfully."""

    event_type: str = "pipeline.completed"
    pipeline_name: str = ""
    entity_id: str | None = None
    stages_completed: int = 0
    total_duration_ms: float | None = None


@dataclass(frozen=True)
class PipelineFailed(Event):
    """Emitted when a pipeline run fails at any stage."""

    event_type: str = "pipeline.failed"
    pipeline_name: str = ""
    failed_stage: str = ""
    entity_id: str | None = None
    error_message: str = ""
    is_retryable: bool = False


# ---------------------------------------------------------------------------
# Agent task events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentTaskStarted(Event):
    """Emitted when an agent begins working on a delegated task."""

    event_type: str = "agent.task_started"
    agent_id: str = ""
    task_description: str = ""
    task_id: str | None = None
    delegated_by: str | None = None


@dataclass(frozen=True)
class AgentTaskCompleted(Event):
    """Emitted when an agent finishes a delegated task successfully."""

    event_type: str = "agent.task_completed"
    agent_id: str = ""
    task_description: str = ""
    task_id: str | None = None
    result_summary: str = ""
    duration_seconds: float | None = None


@dataclass(frozen=True)
class AgentTaskFailed(Event):
    """Emitted when an agent fails to complete a delegated task."""

    event_type: str = "agent.task_failed"
    agent_id: str = ""
    task_description: str = ""
    task_id: str | None = None
    error_message: str = ""
    is_retryable: bool = False


# ---------------------------------------------------------------------------
# Proactive alerts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProactiveAlert(Event):
    """Emitted by the proactive engine when it detects something worth surfacing.

    Alert kinds:
      - briefing: morning/evening summary ready
      - nudge: a task or person needs attention
      - stale: a context card, task, or relationship is going stale
    """

    event_type: str = "proactive.alert"
    alert_kind: str = ""  # "briefing", "nudge", "stale"
    title: str = ""
    body: str = ""
    entity_type: str | None = None  # "task", "person", "card"
    entity_id: str | None = None
    urgency: int = 3  # 1-5, 1 = most urgent
    suggested_action: str | None = None


# ---------------------------------------------------------------------------
# Night shift events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NightShiftStarted(Event):
    """Emitted when the overnight batch processing begins."""

    event_type: str = "nightshift.started"
    scheduled_jobs: tuple[str, ...] = ()
    estimated_duration_minutes: float | None = None


@dataclass(frozen=True)
class NightShiftCompleted(Event):
    """Emitted when overnight batch processing finishes."""

    event_type: str = "nightshift.completed"
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    cards_rebuilt: int = 0
    duration_minutes: float | None = None
    errors: tuple[str, ...] = ()
