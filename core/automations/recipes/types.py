"""Recipe data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecipeVariable:
    """A variable slot in a recipe template that Claude fills in."""

    name: str
    description: str
    type: str  # string, number, boolean, cron, email, url
    required: bool = True
    default: Any = None
    examples: list[str] = field(default_factory=list)


@dataclass
class Recipe:
    """A pre-validated n8n workflow template with variable slots."""

    id: str
    name: str
    description: str
    category: str  # productivity, communication, data, monitoring
    tags: list[str] = field(default_factory=list)
    variables: list[RecipeVariable] = field(default_factory=list)
    required_credentials: list[str] = field(default_factory=list)
    template: dict = field(default_factory=dict)  # n8n workflow JSON
    tested_n8n_version: str = "2.x"

    def variable_names(self) -> list[str]:
        return [v.name for v in self.variables]

    def required_variable_names(self) -> list[str]:
        return [v.name for v in self.variables if v.required]

    def summary(self) -> str:
        """One-line summary for Claude's recipe selection prompt."""
        creds = f" (needs: {', '.join(self.required_credentials)})" if self.required_credentials else ""
        vars_desc = ", ".join(v.name for v in self.variables[:3])
        return f"{self.id}: {self.description}{creds} [vars: {vars_desc}]"
