"""Qareen API — Agent management routes.

List, inspect, activate, and configure agent trust levels.
Discovers agents from three locations:
  - ~/.claude/agents/        (installed — active on this machine)
  - ~/aos/core/agents/       (system — chief, steward, advisor)
  - ~/aos/templates/agents/  (catalog — available to hire)
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, status
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from .schemas import (
    AgentCatalogResponse,
    AgentHealthResponse,
    AgentListResponse,
    AgentOptionsResponse,
    AgentResponse,
    UpdateAgentConfigRequest,
    UpdateTrustRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

INSTALLED_DIR = Path.home() / ".claude" / "agents"
SYSTEM_DIR = Path.home() / "aos" / "core" / "agents"
CATALOG_DIR = Path.home() / "aos" / "templates" / "agents"
TRUST_FILE = Path.home() / ".aos" / "config" / "agent-trust.json"


def _load_trust() -> dict[str, int]:
    """Load persisted trust levels."""
    if TRUST_FILE.is_file():
        try:
            import json
            return json.loads(TRUST_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_trust(data: dict[str, int]) -> None:
    """Persist trust levels to disk."""
    import json
    TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRUST_FILE.write_text(json.dumps(data, indent=2))


AOS_ROOT = Path.home() / "aos"


def _normalize_list(val: Any) -> list[str]:
    """Coerce a value to a list of strings."""
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


def _find_agent_path(agent_id: str) -> Path | None:
    """Find the .md file for an agent across all directories."""
    for d in [INSTALLED_DIR, SYSTEM_DIR, CATALOG_DIR]:
        for candidate in [d / f"{agent_id}.md", d / agent_id / "agent.md"]:
            if candidate.is_file():
                return candidate
    return None


def _is_system_symlink(path: Path) -> bool:
    """Check whether an agent file is a system agent (symlink or in system dir)."""
    return path.is_symlink() or str(path).startswith(str(SYSTEM_DIR))


_FIELD_TO_YAML_KEY: dict[str, str] = {
    "mcp_servers": "mcpServers",
    "reports_to": "reportsTo",
    "permission_mode": "permissionMode",
    "max_turns": "maxTurns",
    "can_spawn": "canSpawn",
    "on_failure": "onFailure",
    "max_retries": "maxRetries",
    "self_contained": "selfContained",
    "disallowed_tools": "disallowedTools",
    "default_trust": "defaultTrust",
}


def _rebuild_agent_md(frontmatter: dict, body: str) -> str:
    """Rebuild an agent .md file from frontmatter dict and body markdown."""
    import yaml
    yaml_str = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---\n{body}"


# System agent defaults (for .md files that lack rich frontmatter)
SYSTEM_DEFAULTS: dict[str, dict[str, Any]] = {
    "chief": {
        "role": "Orchestrator",
        "color": "#BF5AF2",
        "initials": "CH",
        "model": "opus",
        "reports_to": None,
        "scope": "global",
        "skills": ["recall", "work", "review", "step-by-step", "deliberate", "extract", "telegram-admin", "ramble", "ship"],
    },
    "advisor": {
        "role": "Strategy & Analysis",
        "color": "#AF52DE",
        "initials": "AD",
        "model": "opus",
        "reports_to": "chief",
        "scope": "global",
        "skills": ["recall", "review", "deliberate", "session-analysis"],
    },
    "steward": {
        "role": "System Health",
        "color": "#64D2FF",
        "initials": "ST",
        "reports_to": "chief",
        "scope": "global",
        "skills": ["systematic-debugging", "bridge-ops", "report"],
    },
}

# Default hierarchy for catalog agents
CATALOG_HIERARCHY: dict[str, str] = {
    "ops": "steward",
    "technician": "steward",
    "engineer": "chief",
    "developer": "chief",
    "cmo": "chief",
    "onboard": "chief",
    "nuchay": "chief",
}


def _make_initials(name: str) -> str:
    """Generate 2-letter initials from a name."""
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if len(name) >= 2:
        return name[:2].upper()
    return name.upper()


def _parse_agent_md(path: Path, *, source: str = "catalog") -> dict[str, Any]:
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
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                description = stripped[:200]
                break

    # System agents
    system_agents = {"chief", "steward", "advisor"}
    is_system = agent_id in system_agents
    sys_defaults = SYSTEM_DEFAULTS.get(agent_id, {})

    # Determine source
    if is_system:
        source = "system"
    elif agent_id.startswith("c-"):
        source = "community"

    # Normalize tools, skills, mcp to lists
    raw_tools = frontmatter.get("tools", [])
    if isinstance(raw_tools, str):
        raw_tools = [raw_tools] if raw_tools != "*" else ["*"]
    elif not isinstance(raw_tools, list):
        raw_tools = []

    raw_skills = frontmatter.get("skills", None)
    if raw_skills is None:
        raw_skills = sys_defaults.get("skills", [])
    if isinstance(raw_skills, str):
        raw_skills = [raw_skills]
    elif not isinstance(raw_skills, list):
        raw_skills = []

    raw_mcp = frontmatter.get("mcpServers", frontmatter.get("mcp_servers", []))
    if isinstance(raw_mcp, str):
        raw_mcp = [raw_mcp]
    elif not isinstance(raw_mcp, list):
        raw_mcp = []

    role = frontmatter.get("role", sys_defaults.get("role", ""))
    color = frontmatter.get("color", sys_defaults.get("color", ""))
    initials = frontmatter.get("initials", sys_defaults.get("initials", ""))
    if not initials:
        initials = _make_initials(name)

    scope = frontmatter.get("scope", sys_defaults.get("scope", "global"))

    # Hierarchy
    reports_to = frontmatter.get("reportsTo", frontmatter.get("reports_to", None))
    if reports_to is None and not is_system:
        reports_to = CATALOG_HIERARCHY.get(agent_id, "chief")
    if reports_to is None and is_system:
        reports_to = sys_defaults.get("reports_to")

    return {
        "id": agent_id,
        "name": name,
        "role": role,
        "domain": frontmatter.get("domain", ""),
        "description": description,
        "model": frontmatter.get("model", sys_defaults.get("model", "sonnet")),
        "color": color,
        "initials": initials,
        "tools": raw_tools,
        "skills": raw_skills,
        "mcp_servers": raw_mcp,
        "scope": scope,
        "reports_to": reports_to,
        "source": source,
        "is_system": is_system,
        "is_active": True,
        "schedule": frontmatter.get("schedule", {}) if isinstance(frontmatter.get("schedule"), dict) else {},
        "source_path": str(path),
        # Permissions & execution
        "permission_mode": frontmatter.get("permissionMode", frontmatter.get("permission_mode", "default")),
        "max_turns": frontmatter.get("maxTurns", frontmatter.get("max_turns", None)),
        "effort": frontmatter.get("effort", ""),
        # Orchestration
        "can_spawn": _normalize_list(frontmatter.get("canSpawn", frontmatter.get("can_spawn", []))),
        "disallowed_tools": _normalize_list(frontmatter.get("disallowedTools", frontmatter.get("disallowed_tools", []))),
        "isolation": frontmatter.get("isolation", ""),
        "background": bool(frontmatter.get("background", False)),
        "memory": frontmatter.get("memory", ""),
        # Context & dependencies
        "rules": _normalize_list(frontmatter.get("rules", [])),
        "parameters": frontmatter.get("parameters", {}) if isinstance(frontmatter.get("parameters"), dict) else {},
        "services": _normalize_list(frontmatter.get("services", [])),
        "prerequisites": _normalize_list(frontmatter.get("prerequisites", [])),
        # Failure & data flow
        "on_failure": frontmatter.get("onFailure", frontmatter.get("on_failure", "escalate")),
        "max_retries": int(frontmatter.get("maxRetries", frontmatter.get("max_retries", 0))),
        "inputs": _normalize_list(frontmatter.get("inputs", [])),
        "outputs": _normalize_list(frontmatter.get("outputs", [])),
        # Metadata
        "self_contained": bool(frontmatter.get("selfContained", frontmatter.get("self_contained", False))),
        "version": str(frontmatter.get("version", "1.0")),
        "body": body,
    }


def _discover_agents(directory: Path, *, source: str = "catalog") -> list[dict[str, Any]]:
    """Discover agent definitions from a directory."""
    agents = []
    if not directory.is_dir():
        return agents

    for entry in directory.iterdir():
        if entry.suffix == ".md" and entry.is_file():
            # Skip files with invalid agent names
            if entry.stem.startswith("-") or entry.stem.startswith("."):
                continue
            parsed = _parse_agent_md(entry, source=source)
            if parsed:
                agents.append(parsed)
        elif entry.is_dir():
            agent_md = entry / "agent.md"
            if agent_md.is_file():
                parsed = _parse_agent_md(agent_md, source=source)
                if parsed:
                    parsed["id"] = entry.name
                    agents.append(parsed)

    return agents


def _to_response(
    a: dict[str, Any],
    trust_map: dict[str, int] | None = None,
    include_body: bool = False,
) -> AgentResponse:
    """Convert parsed agent dict to API response."""
    trust = (trust_map or {}).get(a["id"])
    return AgentResponse(
        id=a["id"],
        name=a["name"],
        role=a.get("role", ""),
        domain=a.get("domain", ""),
        description=a.get("description", ""),
        model=a.get("model", "sonnet"),
        color=a.get("color", ""),
        initials=a.get("initials", ""),
        tools=a.get("tools", []),
        skills=a.get("skills", []),
        mcp_servers=a.get("mcp_servers", []),
        default_trust=trust if trust is not None else 1,
        scope=a.get("scope", "global"),
        reports_to=a.get("reports_to"),
        source=a.get("source", "catalog"),
        is_system=a.get("is_system", False),
        is_active=a.get("is_active", True),
        schedule=a.get("schedule", {}),
        # Permissions & execution
        permission_mode=a.get("permission_mode", "default"),
        max_turns=a.get("max_turns"),
        effort=a.get("effort", ""),
        # Orchestration
        can_spawn=a.get("can_spawn", []),
        disallowed_tools=a.get("disallowed_tools", []),
        isolation=a.get("isolation", ""),
        background=a.get("background", False),
        memory=a.get("memory", ""),
        # Context & dependencies
        rules=a.get("rules", []),
        parameters=a.get("parameters", {}),
        services=a.get("services", []),
        prerequisites=a.get("prerequisites", []),
        # Failure & data flow
        on_failure=a.get("on_failure", "escalate"),
        max_retries=a.get("max_retries", 0),
        inputs=a.get("inputs", []),
        outputs=a.get("outputs", []),
        # Metadata
        self_contained=a.get("self_contained", False),
        version=a.get("version", "1.0"),
        body=a.get("body") if include_body else None,
    )


@router.get("", response_model=AgentListResponse)
async def list_agents(request: Request) -> AgentListResponse:
    """List all installed agents (active on this machine)."""
    agents_data = _discover_agents(INSTALLED_DIR, source="catalog")

    # Also pull system agents from core/agents/ if not already in installed dir
    if SYSTEM_DIR.is_dir():
        existing_ids = {a["id"] for a in agents_data}
        system_agents = _discover_agents(SYSTEM_DIR, source="system")
        for sa in system_agents:
            if sa["id"] not in existing_ids:
                agents_data.append(sa)

    trust_map = _load_trust()
    agents = [_to_response(a, trust_map) for a in agents_data]

    system_count = sum(1 for a in agents if a.is_system)
    active_count = sum(1 for a in agents if a.is_active)

    return AgentListResponse(
        agents=agents,
        total=len(agents),
        active_count=active_count,
        system_count=system_count,
    )


@router.get("/catalog", response_model=AgentCatalogResponse)
async def list_catalog(request: Request) -> AgentCatalogResponse:
    """List catalog agents available to hire (from templates/agents/)."""
    catalog_data = _discover_agents(CATALOG_DIR, source="catalog")

    # Exclude agents already installed
    installed_ids: set[str] = set()
    if INSTALLED_DIR.is_dir():
        for entry in INSTALLED_DIR.iterdir():
            if entry.suffix == ".md":
                installed_ids.add(entry.stem)
            elif entry.is_dir() and (entry / "agent.md").is_file():
                installed_ids.add(entry.name)

    available = [_to_response(a) for a in catalog_data if a["id"] not in installed_ids]

    return AgentCatalogResponse(
        catalog=available,
        total=len(available),
    )


# ---------------------------------------------------------------------------
# Options endpoints — MUST be before /{agent_id} to avoid path conflicts
# ---------------------------------------------------------------------------


@router.get("/options/tools", response_model=AgentOptionsResponse)
async def list_tool_options() -> AgentOptionsResponse:
    """List available tools: built-in Claude Code tools + MCP tools."""
    import json

    # Built-in Claude Code tools
    tools = [
        "Agent", "Bash", "Edit", "Glob", "Grep", "Read", "Write",
        "NotebookEdit", "TodoRead", "TodoWrite", "WebFetch", "WebSearch",
    ]

    # Add MCP tools from mcp.json
    mcp_json = Path.home() / ".claude" / "mcp.json"
    if mcp_json.is_file():
        try:
            mcp_data = json.loads(mcp_json.read_text())
            servers = mcp_data.get("mcpServers", {})
            for server_name in servers:
                tools.append(f"mcp__{server_name}")
        except Exception:
            pass

    tools.sort()
    return AgentOptionsResponse(items=tools, total=len(tools))


@router.get("/options/skills", response_model=AgentOptionsResponse)
async def list_skill_options() -> AgentOptionsResponse:
    """List available skills from ~/.claude/skills/."""
    skills_dir = Path.home() / ".claude" / "skills"
    skills: list[str] = []

    if skills_dir.is_dir():
        for entry in skills_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                skills.append(entry.name)

    skills.sort()
    return AgentOptionsResponse(items=skills, total=len(skills))


@router.get("/options/mcp-servers", response_model=AgentOptionsResponse)
async def list_mcp_options() -> AgentOptionsResponse:
    """List all known MCP servers — configured + referenced by agents."""
    import json

    servers: set[str] = set()

    # 1. Configured in ~/.claude/mcp.json (actually installed)
    mcp_json = Path.home() / ".claude" / "mcp.json"
    if mcp_json.is_file():
        try:
            mcp_data = json.loads(mcp_json.read_text())
            servers.update(mcp_data.get("mcpServers", {}).keys())
        except Exception:
            pass

    # 2. Referenced by any agent (from frontmatter mcpServers field)
    for directory in [INSTALLED_DIR, SYSTEM_DIR, CATALOG_DIR]:
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if entry.suffix == ".md" and entry.is_file() and not entry.stem.startswith("-"):
                parsed = _parse_agent_md(entry)
                if parsed:
                    servers.update(parsed.get("mcp_servers", []))

    return AgentOptionsResponse(items=sorted(servers), total=len(servers))


@router.get("/options/rules", response_model=AgentOptionsResponse)
async def list_rule_options() -> AgentOptionsResponse:
    """List available rule files from ~/.claude/rules/ and ~/aos/.claude/rules/."""
    rules: list[str] = []
    search_dirs = [
        Path.home() / ".claude" / "rules",
        AOS_ROOT / ".claude" / "rules",
    ]

    for rules_dir in search_dirs:
        if rules_dir.is_dir():
            for entry in rules_dir.iterdir():
                if entry.suffix == ".md" and entry.is_file():
                    rules.append(entry.stem)

    rules = sorted(set(rules))
    return AgentOptionsResponse(items=rules, total=len(rules))


@router.get("/options/services", response_model=AgentOptionsResponse)
async def list_service_options() -> AgentOptionsResponse:
    """List AOS services from LaunchAgents plists."""
    import glob as glob_mod

    pattern = str(Path.home() / "Library" / "LaunchAgents" / "com.aos.*.plist")
    services: list[str] = []

    for plist_path in glob_mod.glob(pattern):
        name = Path(plist_path).stem
        # Strip com.aos. prefix
        if name.startswith("com.aos."):
            services.append(name[8:])

    services.sort()
    return AgentOptionsResponse(items=services, total=len(services))


@router.get("/options/agents", response_model=AgentOptionsResponse)
async def list_agent_options() -> AgentOptionsResponse:
    """List all known agent IDs across installed, system, and catalog dirs."""
    agent_ids: set[str] = set()

    for directory in [INSTALLED_DIR, SYSTEM_DIR, CATALOG_DIR]:
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if entry.suffix == ".md" and entry.is_file():
                if not entry.stem.startswith("-") and not entry.stem.startswith("."):
                    agent_ids.add(entry.stem)
            elif entry.is_dir() and (entry / "agent.md").is_file():
                agent_ids.add(entry.name)

    items = sorted(agent_ids)
    return AgentOptionsResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# Agent detail + config endpoints
# ---------------------------------------------------------------------------


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    request: Request,
    agent_id: str = PathParam(..., description="Agent identifier, e.g. 'chief'"),
) -> AgentResponse | JSONResponse:
    """Get detailed info for a single agent."""
    # Search installed → system → catalog
    for directory, source in [
        (INSTALLED_DIR, "catalog"),
        (SYSTEM_DIR, "system"),
        (CATALOG_DIR, "catalog"),
    ]:
        agent_path = directory / f"{agent_id}.md"
        if not agent_path.is_file():
            agent_path = directory / agent_id / "agent.md"
        if agent_path.is_file():
            data = _parse_agent_md(agent_path, source=source)
            if data:
                return _to_response(data)

    return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)


@router.post("/community/install", status_code=status.HTTP_201_CREATED)
async def install_community_agent(request: Request) -> JSONResponse:
    """Install a community agent by downloading its .md from GitHub."""
    body = await request.json()
    repo = body.get("repo")
    file_path = body.get("file")
    agent_id = body.get("id")

    if not repo or not file_path or not agent_id:
        return JSONResponse({"error": "Missing repo, file, or id"}, status_code=400)

    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", agent_id)
    if not safe_id:
        return JSONResponse({"error": "Invalid agent ID"}, status_code=400)

    target_path = INSTALLED_DIR / f"{safe_id}.md"
    if target_path.exists():
        return JSONResponse({"error": f"Agent '{safe_id}' is already installed"}, status_code=409)

    url = f"https://raw.githubusercontent.com/{repo}/main/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Failed to fetch from GitHub: {resp.status_code}"},
                    status_code=502,
                )
            content = resp.text
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"GitHub fetch error: {exc}"}, status_code=502)

    INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    logger.info("Installed community agent '%s' from %s/%s", safe_id, repo, file_path)

    data = _parse_agent_md(target_path, source="community")
    return JSONResponse(
        {"ok": True, "id": safe_id, "name": data.get("name", safe_id), "installed": True},
        status_code=201,
    )


@router.post("/{agent_id}/activate", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def activate_agent(
    request: Request,
    agent_id: str = PathParam(..., description="Catalog agent ID to activate"),
) -> AgentResponse | JSONResponse:
    """Activate an agent from the catalog by copying it to ~/.claude/agents/."""
    target_path = INSTALLED_DIR / f"{agent_id}.md"
    if target_path.exists():
        return JSONResponse({"error": f"Agent '{agent_id}' is already installed"}, status_code=409)

    catalog_path = CATALOG_DIR / f"{agent_id}.md"
    if not catalog_path.is_file():
        catalog_path = CATALOG_DIR / agent_id / "agent.md"
    if not catalog_path.is_file():
        return JSONResponse({"error": f"Agent not in catalog: {agent_id}"}, status_code=404)

    data = _parse_agent_md(catalog_path, source="catalog")
    if not data:
        return JSONResponse({"error": f"Could not parse agent: {agent_id}"}, status_code=500)

    INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(catalog_path), str(target_path))
    logger.info("Activated agent '%s': %s → %s", agent_id, catalog_path, target_path)

    return _to_response(data)


@router.patch("/{agent_id}/trust", response_model=AgentResponse)
async def update_trust(
    body: UpdateTrustRequest,
    request: Request,
    agent_id: str = PathParam(..., description="Agent ID to update trust for"),
) -> AgentResponse | JSONResponse:
    """Update an agent's trust level and persist to disk."""
    agent_resp = await get_agent(request, agent_id)
    if isinstance(agent_resp, JSONResponse):
        return agent_resp

    # Persist
    trust_map = _load_trust()
    trust_map[agent_id] = body.trust_level
    _save_trust(trust_map)

    agent_resp.default_trust = body.trust_level
    logger.info("Updated trust for '%s' to %d", agent_id, body.trust_level)
    return agent_resp


@router.post("/dispatch")
async def dispatch_agent(request: Request) -> JSONResponse:
    """Dispatch an agent to perform a task.

    Body: { "agent_id": "steward", "task": "summarize my tasks", "context": "..." }
    Returns: { "result": "...", "status": "ok", "duration_ms": 1234 }

    Reads the agent .md, uses its content as system prompt, calls
    claude --print --model sonnet with the task as user message.
    """
    body = await request.json()
    agent_id = body.get("agent_id", "").strip()
    task = body.get("task", "").strip()
    context = body.get("context", "")

    if not agent_id:
        return JSONResponse({"error": "agent_id is required"}, status_code=400)
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    # Find the agent .md file
    agent_path = None
    for directory in [INSTALLED_DIR, SYSTEM_DIR, CATALOG_DIR]:
        candidate = directory / f"{agent_id}.md"
        if candidate.is_file():
            agent_path = candidate
            break
        candidate = directory / agent_id / "agent.md"
        if candidate.is_file():
            agent_path = candidate
            break

    if not agent_path:
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    # Read agent definition for system prompt context
    agent_data = _parse_agent_md(agent_path)
    agent_content = agent_path.read_text(encoding="utf-8", errors="replace")
    # Strip frontmatter for the system prompt
    if agent_content.startswith("---"):
        end = agent_content.find("---", 3)
        if end != -1:
            agent_content = agent_content[end + 3:].lstrip("\n")

    model = body.get("model", agent_data.get("model", "sonnet"))

    # Build user prompt
    user_prompt = task
    if context:
        user_prompt = f"Context: {context}\n\nTask: {task}"

    # Route through execution layer
    try:
        from core.engine.execution.router import ExecutionRouter

        router = ExecutionRouter()
        exec_result = await router.execute(
            agent_id=agent_id,
            prompt=user_prompt,
            model=model,
            system_prompt=agent_content,
        )

        status_code = 200 if exec_result.status == "ok" else (504 if exec_result.status == "timeout" else 500)
        return JSONResponse({
            "result": exec_result.text,
            "status": exec_result.status,
            "agent_id": agent_id,
            "agent_name": agent_data.get("name", agent_id),
            "provider": exec_result.provider,
            "model": exec_result.model,
            "duration_ms": exec_result.duration_ms,
            "tokens_in": exec_result.tokens_in,
            "tokens_out": exec_result.tokens_out,
            "error": exec_result.error,
        }, status_code=status_code)

    except ImportError:
        # Fallback: direct claude CLI if execution router not available
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--model", model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(user_prompt.encode()), timeout=120,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            if proc.returncode != 0:
                return JSONResponse({"result": "", "status": "error", "error": stderr.decode()[:500], "duration_ms": duration_ms}, status_code=500)
            return JSONResponse({"result": stdout.decode().strip(), "status": "ok", "agent_id": agent_id, "agent_name": agent_data.get("name", agent_id), "duration_ms": duration_ms})
        except FileNotFoundError:
            return JSONResponse({"error": "claude CLI not found"}, status_code=503)
        except asyncio.TimeoutError:
            return JSONResponse({"result": "", "status": "timeout", "duration_ms": int((time.monotonic() - start) * 1000)}, status_code=504)


# ---------------------------------------------------------------------------
# Config read/write + health
# ---------------------------------------------------------------------------


@router.get("/{agent_id}/config", response_model=AgentResponse)
async def get_agent_config(
    request: Request,
    agent_id: str = PathParam(..., description="Agent identifier"),
) -> AgentResponse | JSONResponse:
    """Get full agent config including the system prompt body."""
    path = _find_agent_path(agent_id)
    if path is None:
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    source = "system" if agent_id in {"chief", "steward", "advisor"} else "catalog"
    data = _parse_agent_md(path, source=source)
    if not data:
        return JSONResponse({"error": f"Could not parse agent: {agent_id}"}, status_code=500)

    trust_map = _load_trust()
    return _to_response(data, trust_map, include_body=True)


@router.patch("/{agent_id}/config", response_model=AgentResponse)
async def update_agent_config(
    body: UpdateAgentConfigRequest,
    request: Request,
    agent_id: str = PathParam(..., description="Agent identifier"),
) -> AgentResponse | JSONResponse:
    """Update agent config by writing back to the .md file.

    System agents (symlinks or in core/agents/) return 403.
    """
    import yaml

    path = _find_agent_path(agent_id)
    if path is None:
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    if _is_system_symlink(path):
        return JSONResponse(
            {"error": f"Cannot modify system agent '{agent_id}'. Copy to catalog first."},
            status_code=403,
        )

    # Read current file
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return JSONResponse({"error": f"Cannot read agent file: {exc}"}, status_code=500)

    # Parse existing frontmatter + body
    frontmatter: dict[str, Any] = {}
    file_body = content
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            raw_yaml = content[3:end].strip()
            file_body = content[end + 3:].lstrip("\n")
            try:
                fm = yaml.safe_load(raw_yaml)
                if isinstance(fm, dict):
                    frontmatter = fm
            except Exception:
                pass

    # Apply updates from the request
    updates = body.model_dump(exclude_none=True)
    new_body = updates.pop("body", None)
    if new_body is not None:
        file_body = new_body

    for field_name, value in updates.items():
        yaml_key = _FIELD_TO_YAML_KEY.get(field_name, field_name)
        frontmatter[yaml_key] = value

    # Write back
    rebuilt = _rebuild_agent_md(frontmatter, file_body)
    try:
        path.write_text(rebuilt, encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"Cannot write agent file: {exc}"}, status_code=500)

    logger.info("Updated config for agent '%s' at %s", agent_id, path)

    # Re-parse and return
    source = "system" if agent_id in {"chief", "steward", "advisor"} else "catalog"
    data = _parse_agent_md(path, source=source)
    if not data:
        return JSONResponse({"error": "Failed to re-parse agent after update"}, status_code=500)

    trust_map = _load_trust()
    return _to_response(data, trust_map, include_body=True)


@router.get("/{agent_id}/health", response_model=AgentHealthResponse)
async def get_agent_health(
    request: Request,
    agent_id: str = PathParam(..., description="Agent identifier"),
) -> AgentHealthResponse | JSONResponse:
    """Check health of an agent's dependencies (MCP servers, services, skills, rules)."""
    import json

    from .services import KNOWN_SERVICES, _check_launchctl

    path = _find_agent_path(agent_id)
    if path is None:
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    source = "system" if agent_id in {"chief", "steward", "advisor"} else "catalog"
    data = _parse_agent_md(path, source=source)
    if not data:
        return JSONResponse({"error": f"Could not parse agent: {agent_id}"}, status_code=500)

    checks: list[dict[str, Any]] = []
    all_healthy = True

    # Check MCP servers
    mcp_json = Path.home() / ".claude" / "mcp.json"
    configured_mcps: set[str] = set()
    if mcp_json.is_file():
        try:
            configured_mcps = set(json.loads(mcp_json.read_text()).get("mcpServers", {}).keys())
        except Exception:
            pass

    for server in data.get("mcp_servers", []):
        ok = server in configured_mcps
        checks.append({"type": "mcp_server", "name": server, "ok": ok, "message": "configured" if ok else "not found in mcp.json"})
        if not ok:
            all_healthy = False

    # Check services
    for svc in data.get("services", []):
        if svc in KNOWN_SERVICES:
            label = KNOWN_SERVICES[svc].get("label", f"com.aos.{svc}")
            result = _check_launchctl(label)
            ok = result.get("status") == "running"
            checks.append({"type": "service", "name": svc, "ok": ok, "message": result.get("status", "unknown")})
            if not ok:
                all_healthy = False
        else:
            checks.append({"type": "service", "name": svc, "ok": False, "message": "unknown service"})
            all_healthy = False

    # Check skills
    skills_dir = Path.home() / ".claude" / "skills"
    for skill in data.get("skills", []):
        skill_path = skills_dir / skill / "SKILL.md"
        ok = skill_path.is_file()
        checks.append({"type": "skill", "name": skill, "ok": ok, "message": "found" if ok else "SKILL.md missing"})
        if not ok:
            all_healthy = False

    # Check rules
    for rule in data.get("rules", []):
        found = False
        for rules_dir in [Path.home() / ".claude" / "rules", AOS_ROOT / ".claude" / "rules"]:
            if (rules_dir / f"{rule}.md").is_file():
                found = True
                break
        checks.append({"type": "rule", "name": rule, "ok": found, "message": "found" if found else "rule file missing"})
        if not found:
            all_healthy = False

    return AgentHealthResponse(
        agent_id=agent_id,
        healthy=all_healthy,
        checks=checks,
    )
