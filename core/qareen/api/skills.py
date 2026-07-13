"""Qareen API — Skill discovery routes.

Discovers skills from ~/.claude/skills/ by parsing SKILL.md frontmatter.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .schemas import SkillListResponse, SkillResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

SKILLS_DIR = Path.home() / ".claude" / "skills"

# Category inference from skill content/name
_CATEGORY_HINTS: dict[str, str] = {
    "recall": "core",
    "work": "core",
    "review": "core",
    "step-by-step": "workflow",
    "deliberate": "workflow",
    "writing-plans": "workflow",
    "executing-plans": "workflow",
    "dispatching-parallel-agents": "workflow",
    "requesting-code-review": "workflow",
    "receiving-code-review": "workflow",
    "verification-before-completion": "workflow",
    "systematic-debugging": "workflow",
    "skill-creator": "workflow",
    "writing-skills": "workflow",
    "skill-scanner": "workflow",
    "session-analysis": "workflow",
    "architect": "workflow",
    "frontend-design": "domain",
    "marketing": "domain",
    "diagram": "domain",
    "extract": "integration",
    "bridge-ops": "integration",
    "telegram-admin": "integration",
    "obsidian-cli": "integration",
    "ship": "integration",
    "report": "integration",
    "onboard": "core",
    "whats-new": "core",
    "ramble": "core",
}


def _extract_triggers(description: str) -> list[str]:
    """Extract trigger phrases from the description text."""
    triggers: list[str] = []

    # Look for patterns like: Trigger on "X", "Y", "Z"
    # or: TRIGGER when: X, Y, Z
    # or: Triggers on: "X", "Y"
    trigger_patterns = [
        r'[Tt]rigger(?:s)?\s+on[:\s]+(.+?)(?:\.|$)',
        r'TRIGGER\s+when[:\s]+(.+?)(?:\.|$)',
        r'[Tt]rigger(?:s)?\s+on[:\s]+"([^"]+)"',
    ]

    for pat in trigger_patterns:
        m = re.search(pat, description, re.IGNORECASE)
        if m:
            raw = m.group(1)
            # Extract quoted phrases
            quoted = re.findall(r'"([^"]+)"', raw)
            if quoted:
                triggers.extend(quoted)
            elif ',' in raw:
                # Comma-separated without quotes
                triggers.extend(t.strip().strip('"\'') for t in raw.split(',') if t.strip())
            break

    # Also look for /command style triggers
    slash_cmds = re.findall(r'(/\w[\w-]*)', description)
    for cmd in slash_cmds:
        if cmd not in triggers:
            triggers.append(cmd)

    # Deduplicate while preserving order, limit to 5
    seen: set[str] = set()
    unique: list[str] = []
    for t in triggers:
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return unique[:5]


def _parse_skill_md(skill_dir: Path) -> dict[str, Any] | None:
    """Parse a SKILL.md file from a skill directory."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    skill_id = skill_dir.name

    # Parse YAML frontmatter
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

    name = frontmatter.get("name", skill_id.replace("-", " ").title())
    description = frontmatter.get("description", "")

    # If description is missing, try first paragraph of body
    if not description:
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                description = stripped[:300]
                break

    triggers = _extract_triggers(description)
    category = _CATEGORY_HINTS.get(skill_id, "domain")

    # Allowed tools
    raw_tools = frontmatter.get("allowed-tools", frontmatter.get("allowed_tools", ""))
    if isinstance(raw_tools, str):
        allowed_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
    elif isinstance(raw_tools, list):
        allowed_tools = raw_tools
    else:
        allowed_tools = []

    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "triggers": triggers,
        "category": category,
        "allowed_tools": allowed_tools,
        "body": body,
        "is_active": True,
        "source_path": str(skill_md),
    }


def _discover_skills() -> list[dict[str, Any]]:
    """Discover all skills from the skills directory."""
    skills: list[dict[str, Any]] = []
    if not SKILLS_DIR.is_dir():
        return skills

    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            parsed = _parse_skill_md(entry)
            if parsed:
                skills.append(parsed)

    return skills


@router.get("", response_model=SkillListResponse)
async def list_skills() -> SkillListResponse:
    """List all installed skills (body excluded for performance)."""
    skills_data = _discover_skills()
    skills = [
        SkillResponse(
            id=s["id"],
            name=s["name"],
            description=s["description"],
            triggers=s["triggers"],
            category=s.get("category", "domain"),
            allowed_tools=s.get("allowed_tools", []),
            is_active=s["is_active"],
            source_path=s["source_path"],
        )
        for s in skills_data
    ]

    return SkillListResponse(
        skills=skills,
        total=len(skills),
        active_count=sum(1 for s in skills if s.is_active),
    )


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str) -> SkillResponse | JSONResponse:
    """Get a single skill with full body content."""
    from fastapi.responses import JSONResponse

    skill_dir = SKILLS_DIR / skill_id
    if not skill_dir.is_dir():
        return JSONResponse({"error": f"Skill not found: {skill_id}"}, status_code=404)

    parsed = _parse_skill_md(skill_dir)
    if not parsed:
        return JSONResponse({"error": f"Could not parse skill: {skill_id}"}, status_code=500)

    return SkillResponse(
        id=parsed["id"],
        name=parsed["name"],
        description=parsed["description"],
        triggers=parsed["triggers"],
        category=parsed.get("category", "domain"),
        allowed_tools=parsed.get("allowed_tools", []),
        body=parsed.get("body", ""),
        is_active=parsed["is_active"],
        source_path=parsed["source_path"],
    )
