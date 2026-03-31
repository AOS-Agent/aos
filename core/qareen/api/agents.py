"""Qareen API — Agent management routes.

List, inspect, activate, and configure agent trust levels.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request, status
from fastapi.responses import JSONResponse

from .schemas import (
    AgentCatalogResponse,
    AgentListResponse,
    AgentResponse,
    UpdateTrustRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Agent .md files live in ~/.claude/agents/ (activated) and ~/aos/core/agents/ (catalog)
AGENTS_DIR = Path.home() / ".claude" / "agents"
CATALOG_DIR = Path.home() / "aos" / "core" / "agents"


def _parse_agent_md(path: Path) -> dict[str, Any]:
    """Parse an agent .md file and extract metadata from frontmatter and content."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    agent_id = path.stem

    # Parse YAML frontmatter if present
    frontmatter: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            raw_yaml = content[3:end].strip()
            body = content[end + 3:].lstrip("\n")
            try:
                import yaml
                fm = yaml.safe_load(raw_yaml)
                if isinstance(fm, dict):
                    frontmatter = fm
            except Exception:
                pass

    # Extract name from first heading or frontmatter
    name = frontmatter.get("name", "")
    if not name:
        heading_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if heading_match:
            name = heading_match.group(1).strip()
        else:
            name = agent_id.replace("-", " ").title()

    # Extract description from frontmatter or first paragraph
    description = frontmatter.get("description", "")
    if not description:
        # Get first non-heading, non-empty paragraph
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                description = stripped[:200]
                break

    # System agents
    system_agents = {"chief", "steward", "advisor"}
    is_system = agent_id in system_agents

    # Normalize tools and skills to lists
    raw_tools = frontmatter.get("tools", [])
    if isinstance(raw_tools, str):
        raw_tools = [raw_tools] if raw_tools != "*" else ["*"]
    elif not isinstance(raw_tools, list):
        raw_tools = []

    raw_skills = frontmatter.get("skills", [])
    if isinstance(raw_skills, str):
        raw_skills = [raw_skills]
    elif not isinstance(raw_skills, list):
        raw_skills = []

    return {
        "id": agent_id,
        "name": name,
        "domain": frontmatter.get("domain", ""),
        "description": description,
        "model": frontmatter.get("model", "sonnet"),
        "tools": raw_tools,
        "skills": raw_skills,
        "is_system": is_system,
        "is_active": True,
        "schedule": frontmatter.get("schedule", {}) if isinstance(frontmatter.get("schedule"), dict) else {},
        "source_path": str(path),
    }


def _discover_agents(directory: Path) -> list[dict[str, Any]]:
    """Discover agent definitions from a directory."""
    agents = []
    if not directory.is_dir():
        return agents

    for entry in directory.iterdir():
        if entry.suffix == ".md" and entry.is_file():
            parsed = _parse_agent_md(entry)
            if parsed:
                agents.append(parsed)
        elif entry.is_dir():
            # Check for agent.md inside the directory
            agent_md = entry / "agent.md"
            if agent_md.is_file():
                parsed = _parse_agent_md(agent_md)
                if parsed:
                    parsed["id"] = entry.name
                    agents.append(parsed)

    return agents


@router.get("", response_model=AgentListResponse)
async def list_agents(request: Request) -> AgentListResponse:
    """List all agents (system + activated catalog agents)."""
    agents_data = _discover_agents(AGENTS_DIR)

    # Also check catalog for system agents not in ~/.claude/agents/
    if CATALOG_DIR.is_dir():
        existing_ids = {a["id"] for a in agents_data}
        catalog_agents = _discover_agents(CATALOG_DIR)
        for ca in catalog_agents:
            if ca["id"] not in existing_ids and ca.get("is_system"):
                agents_data.append(ca)

    agents = []
    for a in agents_data:
        agents.append(AgentResponse(
            id=a["id"],
            name=a["name"],
            domain=a.get("domain", ""),
            description=a.get("description", ""),
            model=a.get("model", "sonnet"),
            tools=a.get("tools", []),
            skills=a.get("skills", []),
            is_system=a.get("is_system", False),
            is_active=a.get("is_active", True),
            schedule=a.get("schedule", {}),
        ))

    system_count = sum(1 for a in agents if a.is_system)
    active_count = sum(1 for a in agents if a.is_active)

    return AgentListResponse(
        agents=agents,
        total=len(agents),
        active_count=active_count,
        system_count=system_count,
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    request: Request,
    agent_id: str = PathParam(..., description="Agent identifier, e.g. 'chief'"),
) -> AgentResponse | JSONResponse:
    """Get detailed info for a single agent, including trust configuration."""
    # Search in activated agents
    agent_path = AGENTS_DIR / f"{agent_id}.md"
    if not agent_path.is_file():
        # Try as directory
        agent_path = AGENTS_DIR / agent_id / "agent.md"
    if not agent_path.is_file():
        # Try catalog
        agent_path = CATALOG_DIR / f"{agent_id}.md"
    if not agent_path.is_file():
        agent_path = CATALOG_DIR / agent_id / "agent.md"
    if not agent_path.is_file():
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    data = _parse_agent_md(agent_path)
    if not data:
        return JSONResponse({"error": f"Could not parse agent: {agent_id}"}, status_code=500)

    return AgentResponse(
        id=data["id"],
        name=data["name"],
        domain=data.get("domain", ""),
        description=data.get("description", ""),
        model=data.get("model", "sonnet"),
        tools=data.get("tools", []),
        skills=data.get("skills", []),
        is_system=data.get("is_system", False),
        is_active=data.get("is_active", True),
        schedule=data.get("schedule", {}),
    )


@router.post("/{agent_id}/activate", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def activate_agent(
    request: Request,
    agent_id: str = PathParam(..., description="Catalog agent ID to activate"),
) -> AgentResponse | JSONResponse:
    """Activate an agent from the catalog."""
    # Find in catalog
    catalog_path = CATALOG_DIR / f"{agent_id}.md"
    if not catalog_path.is_file():
        catalog_path = CATALOG_DIR / agent_id / "agent.md"
    if not catalog_path.is_file():
        return JSONResponse({"error": f"Agent not in catalog: {agent_id}"}, status_code=404)

    data = _parse_agent_md(catalog_path)
    if not data:
        return JSONResponse({"error": f"Could not parse agent: {agent_id}"}, status_code=500)

    data["is_active"] = True
    return AgentResponse(
        id=data["id"],
        name=data["name"],
        domain=data.get("domain", ""),
        description=data.get("description", ""),
        model=data.get("model", "sonnet"),
        tools=data.get("tools", []),
        skills=data.get("skills", []),
        is_system=data.get("is_system", False),
        is_active=True,
        schedule=data.get("schedule", {}),
    )


@router.patch("/{agent_id}/trust", response_model=AgentResponse)
async def update_trust(
    body: UpdateTrustRequest,
    request: Request,
    agent_id: str = PathParam(..., description="Agent ID to update trust for"),
) -> AgentResponse | JSONResponse:
    """Update an agent's trust level for a specific action type."""
    # For now, return the agent with updated trust info
    # Trust storage is a future implementation
    agent_resp = await get_agent(request, agent_id)
    if isinstance(agent_resp, JSONResponse):
        return agent_resp
    agent_resp.default_trust = body.trust_level
    return agent_resp
