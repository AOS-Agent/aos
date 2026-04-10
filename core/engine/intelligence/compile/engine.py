"""Compilation engine — Pass 2.

Takes an ExtractionResult + optional vault context, runs one Haiku
compilation call, returns a CompilationResult. This is the per-capture,
on-write pass. Cross-capture patterns happen overnight (Part 10) via a
separate Sonnet-powered pass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..content.result import ExtractionResult
from . import prompts
from .llm import LLMError, complete
from .templates import get_template

logger = logging.getLogger(__name__)


class CompilationError(Exception):
    """Raised when the compile pass cannot produce a usable result."""


@dataclass
class CompilationResult:
    """Structured output of one Haiku compilation pass."""

    summary: str = ""
    concepts: list[str] = field(default_factory=list)
    topic: str = ""
    topic_confidence: float = 0.0
    topic_is_new: bool = True
    entities: list[dict[str, Any]] = field(default_factory=list)
    related_captures: list[str] = field(default_factory=list)
    stage_suggestion: int = 1

    # Provenance — for debugging and the Pipeline view
    template_used: str = ""
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "concepts": self.concepts,
            "topic": self.topic,
            "topic_confidence": self.topic_confidence,
            "topic_is_new": self.topic_is_new,
            "entities": self.entities,
            "related_captures": self.related_captures,
            "stage_suggestion": self.stage_suggestion,
            "template_used": self.template_used,
            "model": self.model,
            "provider": self.provider,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "duration_ms": self.duration_ms,
        }


async def compile_capture(
    extraction: ExtractionResult,
    *,
    existing_topics: list[dict] | None = None,
    recent_capture_paths: list[str] | None = None,
    model: str = "haiku",
) -> CompilationResult:
    """Run Pass 2 compilation on an extraction result.

    Args:
        extraction: The ExtractionResult from the content router.
        existing_topics: List of topic dicts from topics.load_index. If
                         omitted, the engine auto-loads from vault/knowledge/indexes/.
        recent_capture_paths: List of capture slugs for related-capture matching.
                              If omitted, the engine auto-loads from vault/knowledge/captures/
                              (last 40 by mtime).
        model: Model reference. Default 'haiku' routes via claude-code provider.

    Returns:
        CompilationResult.

    Raises:
        CompilationError: if the LLM call fails or produces unparseable output.
    """
    template = get_template(extraction.platform)

    if existing_topics is None:
        existing_topics = _autoload_topics()
    if recent_capture_paths is None:
        recent_capture_paths = _autoload_recent_captures()

    user_prompt = prompts.build_compile_prompt(
        platform=extraction.platform,
        title=extraction.title,
        author=extraction.author,
        url=extraction.url,
        content=extraction.content,
        template_hint=template.system_prompt_hint,
        existing_topics=existing_topics,
        recent_capture_paths=recent_capture_paths,
    )

    try:
        response = await complete(
            prompt=user_prompt,
            model=model,
            system_prompt=prompts.SYSTEM_PROMPT,
            max_tokens=1500,
            response_format="json",
        )
    except LLMError as e:
        raise CompilationError(f"LLM call failed: {e}") from e

    data = response.raw_json
    if not isinstance(data, dict):
        raise CompilationError(
            f"LLM returned non-dict JSON: {type(data).__name__}"
        )

    result = CompilationResult(
        summary=(data.get("summary") or "").strip(),
        concepts=_clean_list(data.get("concepts")),
        topic=(data.get("topic") or "").strip().lower(),
        topic_confidence=_clean_float(data.get("topic_confidence"), 0.5),
        topic_is_new=bool(data.get("topic_is_new", True)),
        entities=_clean_entities(data.get("entities")),
        related_captures=_clean_list(data.get("related_captures")),
        stage_suggestion=int(data.get("stage_suggestion") or 1),
        template_used=template.name,
        model=response.model,
        provider=response.provider,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        duration_ms=response.duration_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers — input sanitizers
# ---------------------------------------------------------------------------

def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s and s not in out:
                out.append(s)
    return out


def _clean_float(value: Any, default: float) -> float:
    try:
        f = float(value)
        if 0.0 <= f <= 1.0:
            return f
    except (TypeError, ValueError):
        pass
    return default


def _clean_entities(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        etype = (item.get("type") or "").strip().lower()
        if etype not in ("person", "project", "organization", "product"):
            etype = "person"
        confidence = _clean_float(item.get("confidence"), 0.6)
        out.append({"type": etype, "name": name, "confidence": confidence})
    return out


# ---------------------------------------------------------------------------
# Vault autoload — for callers that don't pass context explicitly
# ---------------------------------------------------------------------------

VAULT_DIR = Path.home() / "vault"


def _autoload_topics() -> list[dict]:
    """Load up to 25 topic indexes from the vault (cheapest, newest first)."""
    indexes_dir = VAULT_DIR / "knowledge" / "indexes"
    if not indexes_dir.is_dir():
        return []
    try:
        from ..topics.builder import load_index  # lazy import
    except Exception:
        return []

    topics: list[dict] = []
    files = sorted(
        indexes_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for f in files[:25]:
        try:
            idx = load_index(f.stem)
            topics.append({
                "slug": idx.slug,
                "title": idx.title,
                "orientation": idx.orientation,
                "doc_count": idx.doc_count,
            })
        except Exception:
            logger.debug("Failed to load topic index %s", f.name, exc_info=True)
    return topics


def _autoload_recent_captures() -> list[str]:
    """List the 40 most-recent capture slugs (filename stems, no extension)."""
    captures_dir = VAULT_DIR / "knowledge" / "captures"
    if not captures_dir.is_dir():
        return []
    files = sorted(
        captures_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [f.stem for f in files[:40]]
