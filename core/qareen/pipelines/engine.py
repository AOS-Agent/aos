"""Qareen Pipeline Engine — Interface.

The pipeline engine loads YAML pipeline definitions, matches incoming
events to triggers, and executes stage sequences. Each stage runs an
action (registered in the ontology or a callable) and passes its output
to the next stage.

Failure handling per stage:
  - skip: log the error and continue to the next stage
  - abort: stop the run and mark it as failed
  - escalate: stop the run, mark as escalated, and surface to operator
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .types import PipelineRun

if TYPE_CHECKING:
    from ..ontology.model import Ontology


class PipelineEngine:
    """Loads, triggers, and executes multi-stage pipelines.

    Pipelines are defined in YAML files within a definitions directory.
    The engine watches for events via the event bus and triggers matching
    pipelines automatically.
    """

    def __init__(self, ontology: Ontology, event_bus: object) -> None:
        """Initialize the pipeline engine.

        Args:
            ontology: The Qareen ontology instance for action execution
                and data access within pipeline stages.
            event_bus: The event bus for subscribing to trigger events
                and emitting pipeline lifecycle events.
        """
        self._ontology = ontology
        self._event_bus = event_bus

    def load_definitions(self, directory: str) -> None:
        """Load pipeline definitions from YAML files in a directory.

        Scans the directory for .yaml/.yml files, parses each into a
        PipelineDefinition, and registers them for trigger matching.
        Existing definitions with the same name are replaced.

        Args:
            directory: Absolute path to the directory containing
                pipeline definition YAML files.

        Raises:
            FileNotFoundError: If the directory does not exist.
            ValueError: If a YAML file contains an invalid definition.
        """
        raise NotImplementedError

    def trigger(self, pipeline_name: str, event: Any) -> PipelineRun:
        """Trigger a named pipeline with an event.

        Creates a PipelineRun and begins executing stages sequentially.
        Each stage receives the event data and outputs from previous stages.

        Emits pipeline.stage_completed events as stages finish, and
        a pipeline.completed or pipeline.failed event when the run ends.

        Args:
            pipeline_name: The name of the pipeline to trigger.
            event: The event that triggered this run. Passed to each
                stage as input context.

        Returns:
            The PipelineRun instance representing this execution.

        Raises:
            KeyError: If no pipeline with the given name is registered.
        """
        raise NotImplementedError

    def get_run(self, run_id: str) -> PipelineRun | None:
        """Retrieve a pipeline run by its id.

        Args:
            run_id: The unique identifier of the pipeline run.

        Returns:
            The PipelineRun if found, or None if no run exists with
            that id.
        """
        raise NotImplementedError

    def list_runs(
        self,
        pipeline_name: str | None = None,
        limit: int = 50,
    ) -> list[PipelineRun]:
        """List pipeline runs, optionally filtered by pipeline name.

        Returns runs in reverse chronological order (most recent first).

        Args:
            pipeline_name: If provided, only return runs for this pipeline.
                If None, return runs across all pipelines.
            limit: Maximum number of runs to return.

        Returns:
            A list of PipelineRun instances, ordered by start time
            descending.
        """
        raise NotImplementedError
