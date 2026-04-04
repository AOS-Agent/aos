"""Recipe library — loads and manages workflow templates.

Recipes are YAML files in this directory. Each defines a pre-validated
n8n workflow template with variable slots that Claude fills in.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .types import Recipe, RecipeVariable

logger = logging.getLogger(__name__)

RECIPES_DIR = Path(__file__).parent


class RecipeLibrary:
    """Loads recipes from YAML files and provides lookup/matching."""

    def __init__(self, recipes_dir: Path | None = None):
        self._dir = recipes_dir or RECIPES_DIR
        self._recipes: dict[str, Recipe] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all .yaml recipe files from the recipes directory."""
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                recipe = self._load_recipe(path)
                self._recipes[recipe.id] = recipe
                logger.debug("Loaded recipe: %s", recipe.id)
            except Exception:
                logger.exception("Failed to load recipe: %s", path.name)

        logger.info("Loaded %d recipes", len(self._recipes))

    def _load_recipe(self, path: Path) -> Recipe:
        """Parse a single recipe YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        variables = []
        for var_data in data.get("variables", []):
            variables.append(RecipeVariable(
                name=var_data["name"],
                description=var_data.get("description", ""),
                type=var_data.get("type", "string"),
                required=var_data.get("required", True),
                default=var_data.get("default"),
                examples=var_data.get("examples", []),
            ))

        template = data.get("template", {})
        if isinstance(template, str):
            template = json.loads(template)

        return Recipe(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            variables=variables,
            required_credentials=data.get("required_credentials", []),
            template=template,
            tested_n8n_version=data.get("tested_n8n_version", "2.x"),
        )

    def get(self, recipe_id: str) -> Recipe | None:
        return self._recipes.get(recipe_id)

    def list_all(self) -> list[Recipe]:
        return list(self._recipes.values())

    def list_summaries(self) -> str:
        """All recipe summaries as a single string (for Claude's prompt)."""
        return "\n".join(r.summary() for r in self._recipes.values())

    def fill_template(self, recipe: Recipe, variables: dict[str, Any]) -> dict:
        """Substitute variables into a recipe template.

        Variables are referenced as {{variable_name}} in string values
        within the template JSON.
        """
        template_str = json.dumps(recipe.template)

        # Apply defaults for missing optional variables
        filled = {}
        for var in recipe.variables:
            if var.name in variables:
                filled[var.name] = variables[var.name]
            elif var.default is not None:
                filled[var.name] = var.default

        # Substitute {{var_name}} patterns
        for name, value in filled.items():
            pattern = "{{" + name + "}}"
            if isinstance(value, str):
                template_str = template_str.replace(pattern, value)
            else:
                # For non-string values, replace the quoted version too
                template_str = template_str.replace(f'"{pattern}"', json.dumps(value))
                template_str = template_str.replace(pattern, str(value))

        return json.loads(template_str)

    def reload(self) -> None:
        """Reload all recipes from disk."""
        self._recipes.clear()
        self._load_all()
