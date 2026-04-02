"""Qareen Pipelines — Type Definitions.

Pipelines are multi-stage processing workflows triggered by events.
Each pipeline is defined in YAML and loaded at startup. When a matching
event arrives, the pipeline engine creates a PipelineRun and executes
stages sequentially.

Examples:
  - Message received → classify → extract entities → route → respond
  - Task completed → update project stats → check goal progress → notify
  - Person mentioned → lookup context card → surface if stale → rebuild
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Pipeline definition (loaded from YAML)
# ---------------------------------------------------------------------------

@dataclass
class PipelineTrigger:
    """Defines when a pipeline should be activated.

    Attributes:
        event_type: The event type that triggers this pipeline
            (e.g. "message.received", "task.completed").
        conditions: Additional conditions that must be true for the
            trigger to fire. Keys are event field names, values are
            the expected values or match patterns.
    """

    event_type: str
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageDefinition:
    """A single stage within a pipeline definition.

    Attributes:
        name: Human-readable stage name (e.g. "classify_intent").
        action: The action to execute — either a registered action
            name or a callable reference.
        params: Static parameters passed to the action. Event data
            and previous stage outputs are injected at runtime.
        on_failure: What to do if this stage fails.
            "skip" — continue to next stage.
            "abort" — stop the pipeline, mark as failed.
            "escalate" — stop and surface to the operator.
        timeout_seconds: Maximum time this stage may run before
            being considered failed.
    """

    name: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    on_failure: str = "abort"  # skip | abort | escalate
    timeout_seconds: int = 30


@dataclass
class PipelineDefinition:
    """A complete pipeline definition, typically loaded from YAML.

    Attributes:
        name: Unique pipeline name (e.g. "message_inbound").
        description: Human-readable description of what this pipeline does.
        trigger: The event trigger that activates this pipeline.
        stages: Ordered list of stages to execute.
    """

    name: str
    description: str
    trigger: PipelineTrigger
    stages: list[StageDefinition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline execution state
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Result of executing a single pipeline stage.

    Attributes:
        stage_name: The name of the stage that was executed.
        status: Outcome — completed, failed, or skipped.
        started: When stage execution began.
        completed: When stage execution finished, or None if still running.
        output: The stage's return value, passed to subsequent stages.
        error: Error message if the stage failed, or None on success.
    """

    stage_name: str
    status: str = "completed"  # completed | failed | skipped
    started: datetime = field(default_factory=datetime.now)
    completed: datetime | None = None
    output: Any = None
    error: str | None = None


@dataclass
class PipelineRun:
    """A single execution of a pipeline.

    Created when a pipeline is triggered and updated as stages complete.

    Attributes:
        id: Unique run identifier (UUID).
        pipeline_name: Name of the pipeline being executed.
        status: Current run status — running, completed, failed, or
            escalated (failed with operator notification).
        started: When the run began.
        completed: When the run finished, or None if still running.
        current_stage: Name of the currently executing stage, or None
            if the run has finished.
        stage_results: Map of stage name to its result. Populated as
            each stage completes.
        trigger_event_id: The id of the event that triggered this run,
            for audit tracing.
    """

    id: str
    pipeline_name: str
    status: str = "running"  # running | completed | failed | escalated
    started: datetime = field(default_factory=datetime.now)
    completed: datetime | None = None
    current_stage: str | None = None
    stage_results: dict[str, StageResult] = field(default_factory=dict)
    trigger_event_id: str | None = None
