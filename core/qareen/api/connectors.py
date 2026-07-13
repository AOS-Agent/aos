"""Qareen API — Connectors routes.

Unified view of all external service connections.
Discovers what's connected on this machine by reading connector
definitions and running health checks.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

AOS_HOME = Path.home() / "aos"


def _get_discoverer():
    """Import the connector discovery engine."""
    sys.path.insert(0, str(AOS_HOME / "core"))
    from infra.connectors.discover import discover_all
    return discover_all


@router.get("")
async def list_connectors() -> JSONResponse:
    """List all connectors with their current status.

    Returns connectors sorted by: connected first, then by tier, then alphabetical.
    Each connector includes: status, capabilities, health check results, automation ideas.
    """
    try:
        discover_all = _get_discoverer()
        connectors = discover_all()

        connected = sum(1 for c in connectors if c.status == "connected")
        partial = sum(1 for c in connectors if c.status == "partial")
        available = sum(1 for c in connectors if c.status == "available")

        return JSONResponse({
            "connectors": [c.to_dict() for c in connectors],
            "summary": {
                "total": len(connectors),
                "connected": connected,
                "partial": partial,
                "available": available,
            },
        })
    except Exception as e:
        logger.exception("Failed to discover connectors")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/node-types")
async def get_node_types() -> JSONResponse:
    """Get available n8n node types based on connected integrations.

    Returns a map of n8n node type -> connector info, plus always-available types.
    Used by the flow editor to show connection status on nodes.
    """
    try:
        sys.path.insert(0, str(AOS_HOME / "core"))
        from automations.connector_bridge import get_available_node_types

        node_types = get_available_node_types()

        connected = sum(1 for v in node_types.values() if v.get("status") == "connected")
        available = sum(1 for v in node_types.values() if v.get("status") in ("available", "partial", "broken"))
        always = sum(1 for v in node_types.values() if v.get("status") == "always")

        return JSONResponse({
            "node_types": node_types,
            "summary": {
                "connected": connected,
                "available": available,
                "always_available": always,
                "total": len(node_types),
            },
        })
    except Exception as e:
        logger.exception("Failed to get node types")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/automation-ideas")
async def get_automation_ideas() -> JSONResponse:
    """Get automation ideas from connected integrations.

    Returns ideas where all required connectors are connected.
    """
    try:
        discover_all = _get_discoverer()
        connectors = discover_all()

        connected_ids = {c.id for c in connectors if c.status in ("connected", "partial")}
        ideas = []

        for c in connectors:
            if c.status not in ("connected", "partial"):
                continue
            for idea in c.automation_ideas:
                required = set(idea.get("required_also", []))
                all_connected = required.issubset(connected_ids)
                ideas.append({
                    "id": f"{c.id}:{idea.get('id', '')}",
                    "name": idea.get("name", ""),
                    "description": idea.get("description", ""),
                    "source_connector": c.id,
                    "source_name": c.name,
                    "required_connectors": list(required | {c.id}),
                    "all_connected": all_connected,
                    "recipe_hint": idea.get("recipe_hint"),
                })

        # Sort: fully connected first, then by name
        ideas.sort(key=lambda x: (not x["all_connected"], x["name"]))

        return JSONResponse({"ideas": ideas, "count": len(ideas)})
    except Exception as e:
        logger.exception("Failed to get automation ideas")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{connector_id}")
async def get_connector(connector_id: str) -> JSONResponse:
    """Get a single connector's status and details."""
    try:
        discover_all = _get_discoverer()
        connectors = discover_all()

        for c in connectors:
            if c.id == connector_id:
                return JSONResponse(c.to_dict())

        return JSONResponse({"error": "Connector not found"}, status_code=404)
    except Exception as e:
        logger.exception("Failed to get connector")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{connector_id}/health")
async def check_connector_health(connector_id: str) -> JSONResponse:
    """Run health checks for a specific connector (on-demand)."""
    try:
        discover_all = _get_discoverer()
        connectors = discover_all()

        for c in connectors:
            if c.id == connector_id:
                return JSONResponse({
                    "id": c.id,
                    "status": c.status,
                    "status_detail": c.status_detail,
                    "health": c.health,
                })

        return JSONResponse({"error": "Connector not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
