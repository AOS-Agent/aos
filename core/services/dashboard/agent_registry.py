"""Agent registry — single source of truth for agent metadata.

Reads from .claude/agents/*.md frontmatter. Merges trust levels from config/trust.yaml.
Supports dynamic creation of new agents.
"""

import re
import time
from pathlib import Path

import yaml

# Auto-assign colors from this palette when creating agents
COLOR_PALETTE = [
    "#e8723a", "#60a5fa", "#34d399", "#fbbf24", "#a78bfa",
    "#f472b6", "#38bdf8", "#4ade80", "#fb923c", "#c084fc",
    "#22d3ee", "#a3e635", "#e879f9", "#facc15", "#2dd4bf",
]

AGENT_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')

AGENT_TEMPLATE = """---
name: {name}
arabic_name: "{arabic_name}"
description: "{description}"
role: {role}
color: "{color}"
scope: {scope}
tools: [{tools}]
model: {model}
---

# {title}

{description}

## Role
{role_description}

## Trust Level
Level 1 — سَنَنظُرُ (We Shall See). All actions verified by operator.
"""


class AgentRegistry:
    """Scans .claude/agents/*.md, parses frontmatter, merges trust data."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        # Agents are installed globally at ~/.claude/agents/
        self.agents_dir = Path.home() / ".claude" / "agents"
        # Trust config lives in user data at ~/.aos/config/
        self.trust_path = Path.home() / ".aos" / "config" / "trust.yaml"
        self._cache: list[dict] | None = None
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # seconds

    def _parse_frontmatter(self, path: Path) -> dict | None:
        """Parse YAML frontmatter and body from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        if not text.startswith("---"):
            return None

        end = text.find("---", 3)
        if end == -1:
            return None

        try:
            meta = yaml.safe_load(text[3:end]) or {}
        except yaml.YAMLError:
            return None

        body = text[end + 3:].strip()
        meta["_body"] = body
        try:
            meta["_path"] = str(path.relative_to(self.workspace))
        except ValueError:
            meta["_path"] = str(path)
        return meta

    def _load_trust(self) -> dict:
        """Load trust data from config/trust.yaml.

        Returns dict of agent_name -> {trust_level, capabilities, metrics}.
        """
        if not self.trust_path.exists():
            return {}
        try:
            data = yaml.safe_load(self.trust_path.read_text()) or {}
            agents = data.get("agents", {})
            result = {}
            for name, info in agents.items():
                result[name] = {
                    "trust_level": info.get("trust_level", 1),
                    "capabilities": info.get("capabilities", {}),
                    "metrics": info.get("metrics", {}),
                }
            return result
        except Exception:
            return {}

    def list_agents(self) -> list[dict]:
        """Return all agents with metadata. Cached for 5 seconds."""
        now = time.time()
        if self._cache is not None and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        trust_levels = self._load_trust()
        agents = []

        if not self.agents_dir.exists():
            return []

        for path in sorted(self.agents_dir.glob("*.md")):
            meta = self._parse_frontmatter(path)
            if not meta or not meta.get("name"):
                continue

            name = meta["name"]
            trust_data = trust_levels.get(name, {})
            agents.append({
                "name": name,
                "arabic_name": meta.get("arabic_name", ""),
                "description": meta.get("description", ""),
                "role": meta.get("role", ""),
                "color": meta.get("color", "#8b8f9a"),
                "scope": meta.get("scope", "global"),
                "project": meta.get("project", ""),
                "model": meta.get("model", "sonnet"),
                "tools": meta.get("tools", []),
                "trust_level": trust_data.get("trust_level", 1) if isinstance(trust_data, dict) else trust_data,
                "capabilities": trust_data.get("capabilities", {}) if isinstance(trust_data, dict) else {},
                "trust_metrics": trust_data.get("metrics", {}) if isinstance(trust_data, dict) else {},
                "_path": meta.get("_path", ""),
            })

        # Sort: global agents first, then by project, then alphabetically
        agents.sort(key=lambda a: (0 if a["scope"] == "global" else 1, a.get("project", ""), a["name"]))

        self._cache = agents
        self._cache_time = now
        return agents

    def get_agent(self, name: str) -> dict | None:
        """Get a single agent with full detail including body text."""
        path = self.agents_dir / f"{name}.md"
        if not path.exists():
            return None

        meta = self._parse_frontmatter(path)
        if not meta:
            return None

        trust_levels = self._load_trust()
        trust_data = trust_levels.get(name, {})
        return {
            "name": meta.get("name", name),
            "arabic_name": meta.get("arabic_name", ""),
            "description": meta.get("description", ""),
            "role": meta.get("role", ""),
            "color": meta.get("color", "#8b8f9a"),
            "scope": meta.get("scope", "global"),
            "project": meta.get("project", ""),
            "model": meta.get("model", "sonnet"),
            "tools": meta.get("tools", []),
            "trust_level": trust_data.get("trust_level", 1) if isinstance(trust_data, dict) else trust_data,
            "capabilities": trust_data.get("capabilities", {}) if isinstance(trust_data, dict) else {},
            "trust_metrics": trust_data.get("metrics", {}) if isinstance(trust_data, dict) else {},
            "body": meta.get("_body", ""),
            "_path": meta.get("_path", ""),
        }

    def create_agent(self, name: str, role: str, description: str = "",
                     arabic_name: str = "", color: str = "",
                     scope: str = "global", project: str = "",
                     model: str = "sonnet", tools: list[str] = None) -> dict:
        """Create a new agent .md file and add to trust.yaml."""
        # Validate name
        if not AGENT_NAME_PATTERN.match(name):
            raise ValueError(f"Invalid agent name '{name}'. Must be lowercase, start with a letter, hyphens allowed.")

        path = self.agents_dir / f"{name}.md"
        if path.exists():
            raise ValueError(f"Agent '{name}' already exists.")

        # Auto-assign color if not provided
        if not color:
            used_colors = [a["color"] for a in self.list_agents()]
            color = self._next_color(used_colors)

        if tools is None:
            tools = ["Read", "Glob", "Grep", "Bash"]

        tools_str = ", ".join(tools)
        title = name.replace("-", " ").title()
        role_description = description or f"{role} agent for the Mac Mini Agent system."

        content = AGENT_TEMPLATE.format(
            name=name,
            arabic_name=arabic_name,
            description=description or f"{role} agent",
            role=role,
            color=color,
            scope=scope,
            tools=tools_str,
            model=model,
            title=title,
            role_description=role_description,
        )

        # Handle project scope
        if scope == "project" and project:
            # Add project field to frontmatter
            content = content.replace(
                f"scope: {scope}",
                f"scope: {scope}\nproject: {project}"
            )

        path.write_text(content)

        # Add to trust.yaml
        self._add_to_trust(name, role)

        # Invalidate cache
        self._cache = None

        return self.get_agent(name)

    def _next_color(self, used_colors: list[str]) -> str:
        """Pick next available color from palette."""
        for color in COLOR_PALETTE:
            if color not in used_colors:
                return color
        # All used — cycle back
        return COLOR_PALETTE[len(used_colors) % len(COLOR_PALETTE)]

    def _add_to_trust(self, name: str, role: str):
        """Add a new agent entry to trust.yaml."""
        if not self.trust_path.exists():
            return

        try:
            data = yaml.safe_load(self.trust_path.read_text()) or {}
        except Exception:
            return

        agents = data.get("agents", {})
        if name not in agents:
            agents[name] = {
                "role": role,
                "trust_level": 1,
                "note": f"Created dynamically. Trust level 1 — verify everything.",
            }
            data["agents"] = agents
            self.trust_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
