"""Personas — distinct voices in a council.

A persona has:
  - a short id (matches @addressing tags: architect, builder, skeptic, dreamer)
  - a one-line lens description
  - a body prompt that primes the persona for the council

Built-in personas live in personas/<id>.md. Operators can override or add new
ones by dropping markdown files in the same directory or in ~/.aos/personas/.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PERSONA_DIR = Path(__file__).parent / "personas"
USER_PERSONA_DIR = Path.home() / ".aos" / "personas"


@dataclass
class Persona:
    id: str               # e.g. "architect"
    lens: str             # one-line lens description
    body: str             # full persona prompt

    @classmethod
    def from_file(cls, path: Path) -> "Persona":
        text = path.read_text()
        lines = text.splitlines()
        # First non-empty line is treated as the lens (after stripping markdown headers)
        lens = ""
        body_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                lens = stripped
                body_start = i + 1
                break
        body = "\n".join(lines[body_start:]).strip()
        return cls(id=path.stem.lower(), lens=lens, body=body)


def _all_persona_files() -> Iterable[Path]:
    if USER_PERSONA_DIR.exists():
        yield from USER_PERSONA_DIR.glob("*.md")
    if PERSONA_DIR.exists():
        yield from PERSONA_DIR.glob("*.md")


def load_persona(name: str) -> Persona:
    """Load a persona by id. User-defined personas override built-ins."""
    seen = set()
    for path in _all_persona_files():
        if path.stem.lower() in seen:
            continue
        seen.add(path.stem.lower())
        if path.stem.lower() == name.lower():
            return Persona.from_file(path)
    raise KeyError(f"Persona {name!r} not found. Drop a markdown file in {USER_PERSONA_DIR} or {PERSONA_DIR}.")


def list_personas() -> list[str]:
    seen = set()
    for path in _all_persona_files():
        seen.add(path.stem.lower())
    return sorted(seen)


BUILTIN_PERSONAS = ("architect", "builder", "skeptic", "dreamer")
