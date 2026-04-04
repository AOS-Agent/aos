"""Workflow generator — turns natural language into n8n workflows.

Uses Claude to:
1. Select the best recipe for the user's description
2. Extract variable values from the description
3. Fill the recipe template
4. Validate the result

Falls back to asking a clarifying question when intent is ambiguous.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .recipes.library import RecipeLibrary
from .recipes.types import Recipe
from .recipes.validate import validate_workflow

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Result of a workflow generation attempt."""

    success: bool
    workflow_json: dict | None = None
    recipe_id: str | None = None
    recipe_name: str | None = None
    variables_used: dict[str, Any] = field(default_factory=dict)
    human_summary: str = ""
    validation_errors: list[str] = field(default_factory=list)
    clarification_needed: str | None = None
    trigger_type: str = "manual"
    trigger_config: dict = field(default_factory=dict)


SYSTEM_PROMPT = """\
You are an automation builder. The user describes what they want automated.
Your job is to select the best recipe and extract the variable values.

Available recipes:
{recipes}

The user has these connected accounts: {accounts}

Rules:
- Pick the recipe that best matches. If none fit, set recipe_id to null.
- Extract variable values from the description. Use defaults when not specified.
- For cron expressions: "every morning" = "0 8 * * *", "every Monday" = "0 9 * * 1",
  "every hour" = "0 * * * *", "twice a day" = "0 8,20 * * *", "every 30 minutes" = "*/30 * * * *"
- If critical information is missing (like a URL), ask ONE clarifying question.
- The workflow_name should be a short, clear name for what the automation does.

Respond with ONLY valid JSON (no markdown, no backticks):
{{
  "recipe_id": "recipe_id_or_null",
  "variables": {{"var_name": "value", ...}},
  "clarification_needed": "question_or_null",
  "human_summary": "One sentence describing what this automation will do",
  "trigger_type": "schedule|webhook|manual",
  "trigger_config": {{"cron": "0 8 * * *"}}
}}
"""


class WorkflowGenerator:
    """Generates n8n workflows from natural language descriptions."""

    def __init__(self, recipe_library: RecipeLibrary):
        self._recipes = recipe_library

    async def generate(
        self,
        description: str,
        connected_accounts: list[str] | None = None,
        extra_context: dict | None = None,
    ) -> GenerationResult:
        """Generate a workflow from a natural language description.

        Args:
            description: User's natural language description.
            connected_accounts: List of connected account types (e.g., ["telegram", "google_workspace"]).
            extra_context: Additional context (user's telegram chat_id, email, etc.).

        Returns:
            GenerationResult with either a workflow or a clarification question.
        """
        accounts = connected_accounts or []
        context = extra_context or {}

        # Build the prompt
        system = SYSTEM_PROMPT.format(
            recipes=self._recipes.list_summaries(),
            accounts=", ".join(accounts) if accounts else "none",
        )

        user_message = description
        if context:
            user_message += f"\n\nAdditional context: {json.dumps(context)}"

        # Call Claude
        try:
            response = await self._call_claude(system, user_message)
        except Exception as e:
            logger.exception("Claude API call failed")
            return GenerationResult(
                success=False,
                human_summary=f"Failed to generate: {e}",
            )

        # Parse Claude's response
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                parsed = json.loads(match.group())
            else:
                return GenerationResult(
                    success=False,
                    human_summary="Failed to parse generation response",
                )

        # If clarification needed, return the question
        clarification = parsed.get("clarification_needed")
        if clarification:
            return GenerationResult(
                success=False,
                clarification_needed=clarification,
                human_summary=parsed.get("human_summary", ""),
                recipe_id=parsed.get("recipe_id"),
            )

        # If no recipe matched
        recipe_id = parsed.get("recipe_id")
        if not recipe_id:
            return GenerationResult(
                success=False,
                human_summary=parsed.get("human_summary", "No matching recipe found"),
                clarification_needed="I don't have a pre-built recipe for that yet. Could you describe a simpler version?",
            )

        # Get the recipe
        recipe = self._recipes.get(recipe_id)
        if not recipe:
            return GenerationResult(
                success=False,
                human_summary=f"Recipe '{recipe_id}' not found",
            )

        # Fill the template
        variables = parsed.get("variables", {})
        try:
            workflow_json = self._recipes.fill_template(recipe, variables)
        except Exception as e:
            return GenerationResult(
                success=False,
                recipe_id=recipe_id,
                recipe_name=recipe.name,
                human_summary=f"Failed to fill recipe template: {e}",
            )

        # Validate
        errors = validate_workflow(workflow_json)

        return GenerationResult(
            success=len(errors) == 0,
            workflow_json=workflow_json,
            recipe_id=recipe_id,
            recipe_name=recipe.name,
            variables_used=variables,
            human_summary=parsed.get("human_summary", ""),
            validation_errors=errors,
            trigger_type=parsed.get("trigger_type", "manual"),
            trigger_config=parsed.get("trigger_config", {}),
        )

    async def _call_claude(self, system: str, user_message: str) -> str:
        """Call Claude via the `claude` CLI (uses Claude Code subscription).

        Falls back to the Anthropic SDK if an API key is available.
        The CLI approach requires no separate API key — it uses the
        operator's existing Claude Code subscription.
        """
        import asyncio
        import shutil
        import subprocess

        # Primary: use `claude -p` CLI (subscription-based, no API key needed)
        claude_bin = shutil.which("claude")
        if claude_bin:
            prompt = f"{system}\n\n---\n\nUser request: {user_message}"
            proc = await asyncio.to_thread(
                subprocess.run,
                [claude_bin, "-p", prompt, "--model", "haiku", "--output-format", "text"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            logger.warning("claude CLI failed (rc=%d): %s", proc.returncode, proc.stderr[:200])

        # Fallback: Anthropic SDK with API key
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            from pathlib import Path
            agent_secret = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"
            result = subprocess.run(
                [str(agent_secret), "get", "ANTHROPIC_API_KEY"],
                capture_output=True, text=True, timeout=5,
            )
            api_key = result.stdout.strip()

        if api_key:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text

        raise RuntimeError(
            "No Claude access available. Install the `claude` CLI or set ANTHROPIC_API_KEY."
        )
