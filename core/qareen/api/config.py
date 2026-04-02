"""Qareen API — Config routes.

Operator, goals, accounts, and integrations configuration endpoints.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .schemas import (
    AccountsResponse,
    GoalListResponse,
    GoalResponse,
    IntegrationsResponse,
    IntegrationSummary,
    KeyResultSchema,
    OperatorResponse,
    UpdateOperatorRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

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
        logger.exception("Failed to load YAML: %s", path)
        return {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Save a dict to a YAML file."""
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


@router.get("/operator", response_model=OperatorResponse)
async def get_operator(request: Request) -> OperatorResponse:
    """Read the operator configuration from operator.yaml."""
    config_path = AOS_DATA / "config" / "operator.yaml"
    data = _load_yaml(config_path)

    comms = data.get("communication", {})

    return OperatorResponse(
        name=data.get("name", "Operator"),
        timezone=data.get("timezone", "America/Chicago"),
        language=comms.get("language", data.get("language", "en")),
        agent_name=data.get("agent_name", "chief"),
        morning_briefing=data.get("morning_briefing", "06:00"),
        evening_checkin=data.get("evening_checkin", "21:00"),
        quiet_hours_start=data.get("quiet_hours_start", "23:00"),
        quiet_hours_end=data.get("quiet_hours_end", "06:00"),
        business_type=data.get("business_type"),
        role=data.get("role"),
    )


@router.patch("/operator", response_model=OperatorResponse)
async def update_operator(body: UpdateOperatorRequest, request: Request) -> OperatorResponse | JSONResponse:
    """Update fields in the operator configuration."""
    config_path = AOS_DATA / "config" / "operator.yaml"
    data = _load_yaml(config_path)

    updates = body.model_dump(exclude_none=True)
    for key, val in updates.items():
        if key == "language":
            if "communication" not in data:
                data["communication"] = {}
            data["communication"]["language"] = val
        elif hasattr(val, "value"):
            data[key] = val.value
        else:
            data[key] = val

    _save_yaml(config_path, data)

    # Return the updated config
    return await get_operator(request)


@router.get("/goals", response_model=GoalListResponse)
async def get_goals(request: Request) -> GoalListResponse:
    """Read all goals from the goals configuration."""
    from ..ontology.types import ObjectType

    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        # Fallback: read from YAML
        config_path = AOS_DATA / "config" / "goals.yaml"
        data = _load_yaml(config_path)
        goals_data = data.get("goals", [])
        goals = []
        for g in goals_data:
            krs = []
            for kr in g.get("key_results", []):
                krs.append(KeyResultSchema(
                    title=kr.get("title", ""),
                    progress=kr.get("progress", 0),
                    target=kr.get("target"),
                ))
            goals.append(GoalResponse(
                id=g.get("id", ""),
                title=g.get("title", ""),
                weight=g.get("weight", 0),
                description=g.get("description"),
                key_results=krs,
                project=g.get("project"),
            ))
        return GoalListResponse(
            goals=goals,
            total_weight=sum(g.weight for g in goals),
        )

    goals = ontology.list(ObjectType.GOAL, limit=50)
    goal_responses = []
    for g in goals:
        krs = []
        for kr in (g.key_results or []):
            krs.append(KeyResultSchema(
                title=kr.title,
                progress=kr.progress,
                target=kr.target,
            ))
        goal_responses.append(GoalResponse(
            id=g.id,
            title=g.title,
            weight=g.weight,
            description=g.description,
            key_results=krs,
            project=g.project,
        ))
    return GoalListResponse(
        goals=goal_responses,
        total_weight=sum(g.weight for g in goals),
    )


@router.get("/accounts", response_model=AccountsResponse)
async def get_accounts(request: Request) -> AccountsResponse:
    """Read accounts configuration (secrets are redacted)."""
    config_path = AOS_DATA / "config" / "accounts.yaml"
    data = _load_yaml(config_path)

    accounts = []
    raw_accounts = data.get("accounts", data.get("integrations", []))
    if isinstance(raw_accounts, dict):
        for name, cfg in raw_accounts.items():
            entry = {"name": name, "type": cfg.get("type", "unknown")}
            if isinstance(cfg, dict):
                entry["enabled"] = cfg.get("enabled", True)
                entry["category"] = cfg.get("category", "")
            # Never expose secrets
            accounts.append(entry)
    elif isinstance(raw_accounts, list):
        for acc in raw_accounts:
            if isinstance(acc, dict):
                entry = {
                    "name": acc.get("name", acc.get("id", "unknown")),
                    "type": acc.get("type", "unknown"),
                    "enabled": acc.get("enabled", True),
                }
                accounts.append(entry)

    return AccountsResponse(
        accounts=accounts,
        total=len(accounts),
    )


@router.get("/integrations", response_model=IntegrationsResponse)
async def get_integrations(request: Request) -> IntegrationsResponse:
    """Read integrations configuration and status."""
    config_path = AOS_DATA / "config" / "integrations.yaml"
    data = _load_yaml(config_path)

    integrations = []
    raw = data.get("integrations", data) if isinstance(data, dict) else {}
    if isinstance(raw, dict):
        for int_id, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            integrations.append(IntegrationSummary(
                id=int_id,
                name=cfg.get("name", int_id),
                category=cfg.get("category", ""),
                is_active=cfg.get("enabled", cfg.get("is_active", False)),
                is_healthy=cfg.get("is_healthy", True),
                capabilities=cfg.get("capabilities", []),
            ))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                integrations.append(IntegrationSummary(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    category=item.get("category", ""),
                    is_active=item.get("enabled", item.get("is_active", False)),
                    is_healthy=item.get("is_healthy", True),
                    capabilities=item.get("capabilities", []),
                ))

    active = sum(1 for i in integrations if i.is_active)
    return IntegrationsResponse(
        integrations=integrations,
        total=len(integrations),
        active_count=active,
    )
