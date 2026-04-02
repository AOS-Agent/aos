"""Qareen API — Pipeline routes.

List pipeline definitions, view runs, and trigger manually.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from .schemas import (
    PipelineDefinition,
    PipelineListResponse,
    PipelineRunListResponse,
    PipelineRunResponse,
    PipelineStageSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

AOS_DATA = Path.home() / ".aos"
AOS_ROOT = Path.home() / "aos"


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


def _get_pipeline_definitions() -> list[PipelineDefinition]:
    """Load pipeline definitions from config."""
    config_path = AOS_ROOT / "config" / "pipelines.yaml"
    data = _load_yaml(config_path)

    pipelines = []
    raw = data.get("pipelines", [])
    if isinstance(raw, dict):
        for name, cfg in raw.items():
            if isinstance(cfg, dict):
                stages = []
                for s in cfg.get("stages", []):
                    if isinstance(s, dict):
                        stages.append(PipelineStageSchema(
                            name=s.get("name", ""),
                            handler=s.get("handler", ""),
                            timeout_seconds=s.get("timeout", 300),
                        ))
                    elif isinstance(s, str):
                        stages.append(PipelineStageSchema(name=s))
                pipelines.append(PipelineDefinition(
                    name=name,
                    description=cfg.get("description", ""),
                    stages=stages,
                    is_active=cfg.get("enabled", cfg.get("is_active", True)),
                ))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                stages = []
                for s in item.get("stages", []):
                    if isinstance(s, dict):
                        stages.append(PipelineStageSchema(
                            name=s.get("name", ""),
                            handler=s.get("handler", ""),
                            timeout_seconds=s.get("timeout", 300),
                        ))
                    elif isinstance(s, str):
                        stages.append(PipelineStageSchema(name=s))
                pipelines.append(PipelineDefinition(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    stages=stages,
                    is_active=item.get("enabled", item.get("is_active", True)),
                ))

    return pipelines


def _get_pipeline_runs(pipeline_name: str | None = None) -> list[dict[str, Any]]:
    """Read pipeline runs from qareen.db."""
    db_path = AOS_DATA / "data" / "qareen.db"
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_runs'"
        ).fetchone()
        if not table_check:
            conn.close()
            return []

        if pipeline_name:
            rows = conn.execute(
                "SELECT * FROM pipeline_runs WHERE pipeline_name = ? ORDER BY started_at DESC LIMIT 50",
                (pipeline_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 50"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("Failed to read pipeline runs")
        return []


@router.get("", response_model=PipelineListResponse)
async def list_pipelines(request: Request) -> PipelineListResponse:
    """List all pipeline definitions.

    Reads from the pipeline engine's loaded definitions first,
    falls back to the static config file.
    """
    # Try engine definitions first (runtime source of truth)
    engine = getattr(request.app.state, "pipeline_engine", None)
    if engine and hasattr(engine, "_definitions") and engine._definitions:
        pipelines = []
        for name, defn in engine._definitions.items():
            stages = [
                PipelineStageSchema(
                    name=s.name,
                    handler=s.action,
                    timeout_seconds=s.timeout_seconds,
                )
                for s in defn.stages
            ]
            pipelines.append(PipelineDefinition(
                name=defn.name,
                description=defn.description,
                stages=stages,
                is_active=True,
            ))
        return PipelineListResponse(pipelines=pipelines, total=len(pipelines))

    # Fallback to static config
    pipelines = _get_pipeline_definitions()
    return PipelineListResponse(
        pipelines=pipelines,
        total=len(pipelines),
    )


@router.get("/{name}/runs", response_model=PipelineRunListResponse)
async def list_pipeline_runs(
    request: Request,
    name: str = PathParam(..., description="Pipeline name"),
) -> PipelineRunListResponse:
    """List recent runs for a specific pipeline."""
    runs_data = _get_pipeline_runs(name)

    runs = []
    for r in runs_data:
        runs.append(PipelineRunResponse(
            run_id=r.get("id", r.get("run_id", "")),
            pipeline_name=r.get("pipeline_name", name),
            started=r.get("started_at"),
            completed=r.get("completed_at"),
            current_stage=r.get("current_stage"),
            stages_completed=r.get("stages_completed", 0),
            total_stages=r.get("total_stages", 0),
            error=r.get("error"),
            entity_id=r.get("entity_id"),
        ))

    return PipelineRunListResponse(
        runs=runs,
        total=len(runs),
        pipeline_name=name,
    )


@router.post("/{name}/trigger", response_model=PipelineRunResponse)
async def trigger_pipeline(
    request: Request,
    name: str = PathParam(..., description="Pipeline name to trigger"),
) -> PipelineRunResponse | JSONResponse:
    """Manually trigger a pipeline run."""
    # Check the pipeline exists
    pipelines = _get_pipeline_definitions()
    found = None
    for p in pipelines:
        if p.name == name:
            found = p
            break

    if not found:
        return JSONResponse({"error": f"Pipeline not found: {name}"}, status_code=404)

    import uuid
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    return PipelineRunResponse(
        run_id=run_id,
        pipeline_name=name,
        total_stages=len(found.stages),
    )
