"""Qareen Context Store — persistent shared state across all qareen surfaces.

Companion sessions write to it (focus, decisions, entities).
Quick Assist and other pages read from it.
Backed by a single JSON file at ~/.aos/qareen_context.json.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".aos", "qareen_context.json")

_DEFAULT_LEARNING: dict[str, float] = {
    "task": 0.70,
    "decision": 0.80,
    "idea": 0.50,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class QareenContext:
    """Full qareen context — serialised to/from a single JSON file."""

    # From Companion sessions
    active_session_id: str | None = None
    paused_session_ids: list[str] = field(default_factory=list)
    focus: str | None = None
    active_topics: list[str] = field(default_factory=list)
    recent_decisions: list[dict[str, Any]] = field(default_factory=list)
    # Each decision: {"text": str, "thread": str, "timestamp": str}

    # From Quick Assist + all surfaces
    recent_actions: list[dict[str, Any]] = field(default_factory=list)
    # Each: {"input": str, "action_id": str, "spoken": str, "page": str, "timestamp": str}

    # Entity mentions
    recent_entities: list[dict[str, Any]] = field(default_factory=list)
    # Each: {
    #   "name": str,
    #   "type": str,
    #   "entity_id": str | None,
    #   "last_mentioned": str,
    #   # Phase 5 enrichment (populated when type == "person", optional):
    #   "tier": str | None,                 # core/active/fading/... or None
    #   "context_tags": list[str],          # top 3 tags from classification
    #   "days_since_last": int | None,      # recency from profile
    #   "channels_active": list[str],       # top 3 active channels
    # }

    # Navigation
    page_history: list[str] = field(default_factory=list)

    # Learning state — classification thresholds
    learning: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_LEARNING))

    last_updated: str = ""


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

_MAX_TOPICS = 5
_MAX_DECISIONS = 10
_MAX_ACTIONS = 20
_MAX_ENTITIES = 20
_MAX_PAGES = 10

# Learning threshold bounds
_THRESHOLD_MIN = 0.30
_THRESHOLD_MAX = 0.95
_APPROVAL_STEP = 0.02
_DISMISSAL_STEP = 0.03


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class QareenContextStore:
    """Read/write interface for the qareen context JSON file.

    Thread-safety note: this is designed for a single-process FastAPI server.
    File I/O is synchronous and fast (<1ms for a small JSON document).
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else Path(_DEFAULT_PATH)

    # -- public API ---------------------------------------------------------

    def get(self) -> QareenContext:
        """Load and return the current context (defaults if missing/corrupt)."""
        return self._load()

    def update(self, **fields: Any) -> QareenContext:
        """Update specific top-level fields on the context and persist.

        Only known QareenContext fields are applied; unknown keys are ignored.
        List fields with max-length constraints are trimmed after update.
        """
        ctx = self._load()
        valid_fields = {f.name for f in ctx.__dataclass_fields__.values()}  # type: ignore[attr-defined]

        for key, value in fields.items():
            if key in valid_fields:
                setattr(ctx, key, value)

        # Enforce limits on list fields that may have been bulk-set
        ctx.active_topics = ctx.active_topics[:_MAX_TOPICS]
        ctx.recent_decisions = ctx.recent_decisions[:_MAX_DECISIONS]
        ctx.recent_actions = ctx.recent_actions[:_MAX_ACTIONS]
        ctx.recent_entities = ctx.recent_entities[:_MAX_ENTITIES]
        ctx.page_history = ctx.page_history[:_MAX_PAGES]

        ctx.last_updated = _now_iso()
        self._save(ctx)
        return ctx

    def add_action(self, action: dict[str, Any]) -> None:
        """Append an action entry, trimming to the most recent 20."""
        ctx = self._load()
        if "timestamp" not in action:
            action["timestamp"] = _now_iso()
        ctx.recent_actions.append(action)
        ctx.recent_actions = ctx.recent_actions[-_MAX_ACTIONS:]
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def add_entity(self, entity: dict[str, Any]) -> None:
        """Upsert an entity by name. If it exists, update last_mentioned. Trim to 20."""
        ctx = self._load()
        name = entity.get("name", "")
        if not name:
            return

        now = _now_iso()
        entity.setdefault("last_mentioned", now)

        # Upsert: find existing by name (case-insensitive)
        name_lower = name.lower()
        found = False
        for i, existing in enumerate(ctx.recent_entities):
            if existing.get("name", "").lower() == name_lower:
                ctx.recent_entities[i] = {**existing, **entity, "last_mentioned": now}
                found = True
                break

        if not found:
            ctx.recent_entities.append(entity)

        # Trim oldest (front of list) to keep most recent
        ctx.recent_entities = ctx.recent_entities[-_MAX_ENTITIES:]
        ctx.last_updated = now
        self._save(ctx)

    def add_decision(self, decision: dict[str, Any]) -> None:
        """Append a decision, trimming to the most recent 10."""
        ctx = self._load()
        if "timestamp" not in decision:
            decision["timestamp"] = _now_iso()
        ctx.recent_decisions.append(decision)
        ctx.recent_decisions = ctx.recent_decisions[-_MAX_DECISIONS:]
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def set_focus(self, focus: str | None) -> None:
        """Set or clear the current work focus."""
        ctx = self._load()
        ctx.focus = focus
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def add_page(self, page: str) -> None:
        """Append a page visit to history, trimming to 10."""
        if not page:
            return
        ctx = self._load()
        ctx.page_history.append(page)
        ctx.page_history = ctx.page_history[-_MAX_PAGES:]
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def record_approval(self, classification: str) -> None:
        """Lower the threshold for a classification by the approval step (min 0.30)."""
        if not classification:
            return
        ctx = self._load()
        current = ctx.learning.get(classification, _DEFAULT_LEARNING.get(classification, 0.70))
        ctx.learning[classification] = round(max(_THRESHOLD_MIN, current - _APPROVAL_STEP), 4)
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def record_dismissal(self, classification: str) -> None:
        """Raise the threshold for a classification by the dismissal step (max 0.95)."""
        if not classification:
            return
        ctx = self._load()
        current = ctx.learning.get(classification, _DEFAULT_LEARNING.get(classification, 0.70))
        ctx.learning[classification] = round(min(_THRESHOLD_MAX, current + _DISMISSAL_STEP), 4)
        ctx.last_updated = _now_iso()
        self._save(ctx)

    def get_threshold(self, classification: str) -> float:
        """Return the current threshold for a classification."""
        ctx = self._load()
        return ctx.learning.get(classification, _DEFAULT_LEARNING.get(classification, 0.70))

    # -- persistence --------------------------------------------------------

    def _save(self, ctx: QareenContext) -> None:
        """Write the context to the JSON file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(ctx)
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            tmp.replace(self._path)
        except Exception:
            logger.exception("Failed to save qareen context to %s", self._path)

    def _load(self) -> QareenContext:
        """Read the context from JSON. Returns defaults on missing/corrupt file."""
        if not self._path.exists():
            return QareenContext()

        try:
            with open(self._path) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt or unreadable qareen context at %s: %s", self._path, exc)
            return QareenContext()

        if not isinstance(raw, dict):
            logger.warning("Qareen context file is not a JSON object, returning defaults")
            return QareenContext()

        return _dict_to_context(raw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _dict_to_context(data: dict[str, Any]) -> QareenContext:
    """Manually construct a QareenContext from a dict, tolerating missing/extra keys."""
    return QareenContext(
        active_session_id=data.get("active_session_id"),
        paused_session_ids=_as_list(data.get("paused_session_ids", [])),
        focus=data.get("focus"),
        active_topics=_as_list(data.get("active_topics", [])),
        recent_decisions=_as_list(data.get("recent_decisions", [])),
        recent_actions=_as_list(data.get("recent_actions", [])),
        recent_entities=_as_list(data.get("recent_entities", [])),
        page_history=_as_list(data.get("page_history", [])),
        learning=_as_learning(data.get("learning", {})),
        last_updated=data.get("last_updated", ""),
    )


def _as_list(val: Any) -> list:
    """Coerce a value to a list, returning empty list for non-list inputs."""
    return val if isinstance(val, list) else []


def _as_learning(val: Any) -> dict[str, float]:
    """Coerce a value to a learning dict, filling in defaults for missing keys."""
    base = dict(_DEFAULT_LEARNING)
    if isinstance(val, dict):
        for k, v in val.items():
            if isinstance(k, str):
                try:
                    base[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return base
