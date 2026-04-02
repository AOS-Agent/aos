"""Qareen Pipeline Engine.

Loads YAML pipeline definitions, matches incoming events to triggers,
and executes stage sequences. Each stage runs a registered action callable
and passes its output to the next stage.

Failure modes per stage: skip (continue), abort (stop), escalate (stop + alert).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, TYPE_CHECKING

import yaml

from .types import (
    PipelineDefinition,
    PipelineRun,
    PipelineTrigger,
    StageDefinition,
    StageResult,
)

if TYPE_CHECKING:
    from ..events.bus import EventBus
    from ..events.types import Event
    from ..ontology.model import Ontology

logger = logging.getLogger(__name__)

ActionHandler = Callable[..., Coroutine[Any, Any, Any]]


class PipelineEngine:
    """Loads, triggers, and executes multi-stage pipelines."""

    def __init__(self, ontology: Ontology, event_bus: EventBus) -> None:
        self._ontology = ontology
        self._event_bus = event_bus
        self._definitions: dict[str, PipelineDefinition] = {}
        self._runs: dict[str, PipelineRun] = {}
        self._actions: dict[str, ActionHandler] = {}

    # -- Action registration ------------------------------------------------

    def register_action(self, name: str, handler: ActionHandler) -> None:
        """Register an async action handler by name."""
        self._actions[name] = handler
        logger.debug("Registered pipeline action: %s", name)

    # -- Definition loading -------------------------------------------------

    async def load_definitions(self, directory: str) -> None:
        """Load .yaml/.yml pipeline definitions from *directory*."""
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Definitions dir not found: {directory}")

        loaded = 0
        for fp in sorted(dir_path.iterdir()):
            if fp.suffix not in (".yaml", ".yml"):
                continue
            try:
                raw = yaml.safe_load(fp.read_text())
                if not raw or not isinstance(raw, dict):
                    logger.warning("Skipping empty/invalid YAML: %s", fp)
                    continue
                defn = self._parse_definition(raw, str(fp))
                self._definitions[defn.name] = defn
                loaded += 1
                self._event_bus.subscribe(defn.trigger.event_type, self._on_event)
            except Exception:
                logger.exception("Failed to load pipeline def: %s", fp)

        logger.info("Loaded %d pipeline definitions from %s", loaded, directory)

    def _parse_definition(self, raw: dict[str, Any], source: str) -> PipelineDefinition:
        """Parse a raw YAML dict into a PipelineDefinition."""
        name = raw.get("name")
        if not name:
            raise ValueError(f"Pipeline missing 'name' in {source}")

        trig = raw.get("trigger", {})
        trigger = PipelineTrigger(
            event_type=trig.get("event_type", ""),
            conditions=trig.get("conditions") or {},
        )

        stages = []
        for s in raw.get("stages", []):
            stages.append(StageDefinition(
                name=s["name"],
                action=s["action"],
                params=s.get("params", {}),
                on_failure=s.get("on_failure", "abort"),
                timeout_seconds=s.get("timeout_seconds", 30),
            ))

        return PipelineDefinition(
            name=name,
            description=raw.get("description", ""),
            trigger=trigger,
            stages=stages,
        )

    # -- Event matching -----------------------------------------------------

    def _match_trigger(self, event: Event, trigger: PipelineTrigger) -> bool:
        """Return True if *event* satisfies the trigger's type and conditions."""
        if event.event_type != trigger.event_type:
            return False
        for key, expected in trigger.conditions.items():
            actual = event.payload.get(key)
            if actual is None:
                actual = getattr(event, key, None)
            if actual is None:
                return False
            if str(actual) != str(expected) and actual != expected:
                return False
        return True

    async def _on_event(self, event: Event) -> None:
        """Bus callback — fire every pipeline whose trigger matches."""
        for defn in self._definitions.values():
            if self._match_trigger(event, defn.trigger):
                try:
                    await self.trigger(defn.name, event)
                except Exception:
                    logger.exception(
                        "Pipeline '%s' trigger failed for '%s'",
                        defn.name, event.event_type,
                    )

    # -- Pipeline execution -------------------------------------------------

    async def trigger(self, pipeline_name: str, event: Any) -> PipelineRun:
        """Create a PipelineRun and execute stages sequentially."""
        if pipeline_name not in self._definitions:
            raise KeyError(f"No pipeline '{pipeline_name}'")

        defn = self._definitions[pipeline_name]
        run_id = str(uuid.uuid4())

        run = PipelineRun(
            id=run_id,
            pipeline_name=pipeline_name,
            status="running",
            started=datetime.now(),
            trigger_event_id=getattr(event, "event_type", str(event)),
        )
        self._runs[run_id] = run

        await self._emit("pipeline.started", {
            "pipeline_name": pipeline_name, "run_id": run_id,
        })
        logger.info("Pipeline '%s' started (run=%s)", pipeline_name, run_id)

        outputs: dict[str, Any] = {}

        for i, stage in enumerate(defn.stages):
            run.current_stage = stage.name
            result = await self._exec_stage(
                stage, event, outputs, i, len(defn.stages), pipeline_name,
            )
            run.stage_results[stage.name] = result

            if result.status == "completed":
                outputs[stage.name] = result.output
            elif result.status == "skipped":
                outputs[stage.name] = None
            elif result.status == "failed":
                if stage.on_failure == "abort":
                    run.status = "failed"
                    run.completed = datetime.now()
                    run.current_stage = None
                    await self._emit("pipeline.failed", {
                        "pipeline_name": pipeline_name, "run_id": run_id,
                        "failed_stage": stage.name,
                        "error_message": result.error or "unknown",
                    })
                    return run
                elif stage.on_failure == "escalate":
                    run.status = "escalated"
                    run.completed = datetime.now()
                    run.current_stage = None
                    await self._emit("pipeline.failed", {
                        "pipeline_name": pipeline_name, "run_id": run_id,
                        "failed_stage": stage.name,
                        "error_message": result.error or "unknown",
                        "escalated": True,
                    })
                    return run
                # on_failure == "skip": continue

        run.status = "completed"
        run.completed = datetime.now()
        run.current_stage = None

        total_ms = (run.completed - run.started).total_seconds() * 1000
        await self._emit("pipeline.completed", {
            "pipeline_name": pipeline_name, "run_id": run_id,
            "stages_completed": len(run.stage_results),
            "total_duration_ms": total_ms,
        })
        logger.info("Pipeline '%s' done in %.0fms (run=%s)", pipeline_name, total_ms, run_id)
        return run

    async def _exec_stage(
        self,
        stage: StageDefinition,
        event: Any,
        prev_outputs: dict[str, Any],
        idx: int,
        total: int,
        pipeline_name: str,
    ) -> StageResult:
        """Execute one stage with timeout and per-stage failure handling."""
        started = datetime.now()
        handler = self._actions.get(stage.action)

        if handler is None:
            err = f"No handler for action '{stage.action}'"
            logger.warning(err)
            status = "skipped" if stage.on_failure == "skip" else "failed"
            return StageResult(
                stage_name=stage.name, status=status,
                started=started, completed=datetime.now(), error=err,
            )

        try:
            output = await asyncio.wait_for(
                handler(event=event, params=stage.params, outputs=prev_outputs),
                timeout=stage.timeout_seconds,
            )
            completed = datetime.now()
            ms = (completed - started).total_seconds() * 1000
            await self._emit("pipeline.stage_completed", {
                "pipeline_name": pipeline_name, "stage_name": stage.name,
                "stage_index": idx, "total_stages": total, "duration_ms": ms,
            })
            return StageResult(
                stage_name=stage.name, status="completed",
                started=started, completed=completed, output=output,
            )

        except asyncio.TimeoutError:
            err = f"Stage '{stage.name}' timed out ({stage.timeout_seconds}s)"
            logger.warning(err)
            status = "skipped" if stage.on_failure == "skip" else "failed"
            return StageResult(
                stage_name=stage.name, status=status,
                started=started, completed=datetime.now(), error=err,
            )

        except Exception as exc:
            err = f"Stage '{stage.name}' raised: {exc}"
            logger.exception(err)
            status = "skipped" if stage.on_failure == "skip" else "failed"
            return StageResult(
                stage_name=stage.name, status=status,
                started=started, completed=datetime.now(), error=err,
            )

    # -- Run queries --------------------------------------------------------

    def get_run(self, run_id: str) -> PipelineRun | None:
        """Retrieve a pipeline run by id."""
        return self._runs.get(run_id)

    def list_runs(
        self, pipeline_name: str | None = None, limit: int = 50,
    ) -> list[PipelineRun]:
        """List runs, most recent first, optionally filtered by pipeline."""
        runs = list(self._runs.values())
        if pipeline_name is not None:
            runs = [r for r in runs if r.pipeline_name == pipeline_name]
        runs.sort(key=lambda r: r.started, reverse=True)
        return runs[:limit]

    # -- Helpers ------------------------------------------------------------

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit a pipeline lifecycle event on the bus."""
        if self._event_bus is None:
            return
        from ..events.types import Event
        evt = Event(event_type=event_type, source="pipeline_engine", payload=data)
        await self._event_bus.emit(evt)
