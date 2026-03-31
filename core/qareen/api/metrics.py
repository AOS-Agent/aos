"""Qareen API — Metrics routes.

List and query tracked metrics.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request
from fastapi.responses import JSONResponse

from .schemas import MetricListResponse, MetricResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

AOS_DATA = Path.home() / ".aos"


def _get_metrics_from_db() -> list[dict[str, Any]]:
    """Read metrics from qareen.db metrics table, if it exists."""
    db_path = AOS_DATA / "data" / "qareen.db"
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Check if metrics table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'"
        ).fetchone()
        if not table_check:
            conn.close()
            return []

        rows = conn.execute(
            "SELECT * FROM metrics ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("Failed to read metrics from DB")
        return []


@router.get("", response_model=MetricListResponse)
async def list_metrics(request: Request) -> MetricListResponse:
    """List all tracked metrics with their current values."""
    raw_metrics = _get_metrics_from_db()

    if not raw_metrics:
        # Return empty but valid response
        return MetricListResponse(metrics=[], total=0)

    # Group by metric name
    by_name: dict[str, list[dict]] = {}
    for m in raw_metrics:
        name = m.get("name", "unknown")
        by_name.setdefault(name, []).append(m)

    metrics = []
    for name, points in by_name.items():
        current = points[0].get("value") if points else None
        metrics.append(MetricResponse(
            name=name,
            description=points[0].get("description", "") if points else "",
            unit=points[0].get("unit", "") if points else "",
            current_value=current,
            data_points=[],
        ))

    return MetricListResponse(metrics=metrics, total=len(metrics))


@router.get("/{name}", response_model=MetricResponse)
async def get_metric(
    request: Request,
    name: str = PathParam(..., description="Metric name, e.g. 'tasks.completed.daily'"),
) -> MetricResponse | JSONResponse:
    """Get a single metric with its historical data points."""
    db_path = AOS_DATA / "data" / "qareen.db"
    if not db_path.exists():
        return MetricResponse(name=name, description="", unit="", data_points=[])

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'"
        ).fetchone()
        if not table_check:
            conn.close()
            return MetricResponse(name=name, description="", unit="", data_points=[])

        rows = conn.execute(
            "SELECT * FROM metrics WHERE name = ? ORDER BY timestamp DESC LIMIT 100",
            (name,),
        ).fetchall()
        conn.close()

        if not rows:
            return MetricResponse(name=name, description="", unit="", data_points=[])

        current = rows[0]["value"] if rows else None
        return MetricResponse(
            name=name,
            description=rows[0].get("description", "") if rows else "",
            unit=rows[0].get("unit", "") if rows else "",
            current_value=current,
            data_points=[],
        )
    except Exception:
        logger.exception("Failed to get metric %s", name)
        return MetricResponse(name=name, description="", unit="", data_points=[])
