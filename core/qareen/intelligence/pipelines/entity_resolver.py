"""Entity Resolver Pipeline — resolve entity names from thought-units.

Subscribes to stream.unit events. For each entity name in the unit's
entities list, attempts to resolve against the People ontology. Falls
back gracefully when the ontology is unavailable.

When a name successfully resolves to a ``person_id`` at ≥0.6 confidence,
the pipeline also fetches a lightweight classification + profile summary
from the People Intelligence subsystem (Phase 4) and attaches those
fields to the entity record written to the context store. This makes
companion chat aware of *who* the operator is talking about — their
tier, top context tags, and recent activity.

Emits: entity.resolved
Writes: context_store.add_entity()
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ...events.types import Event
from .base import Pipeline

logger = logging.getLogger(__name__)


# ── Lazy import of the People Intelligence subsystem ─────────────────
# The intel subsystem is a soft dependency — if it cannot be imported
# (fresh install, partial migration, etc.), the companion must still
# resolve names without enrichment. Anything raised here is swallowed
# and the pipeline degrades gracefully to its pre-Phase-5 behavior.
try:  # pragma: no cover — exercised by tests via monkey-patching
    from core.engine.people.intel.profiler import ProfileBuilder
    from core.engine.people.intel.runner import ClassifierRunner

    _intel_available = True
except Exception:  # pragma: no cover
    ClassifierRunner = None  # type: ignore[assignment,misc]
    ProfileBuilder = None  # type: ignore[assignment,misc]
    _intel_available = False


class EntityResolverPipeline(Pipeline):
    """Resolve entity names mentioned in thought-units."""

    def __init__(self, bus, context_store=None, people_adapter=None):
        super().__init__(bus, context_store)
        self._people = people_adapter
        # Per-instance lazy intel components — built on first use so
        # pipelines that never resolve a person pay zero cost.
        self._classifier_runner: Any = None
        self._profile_builder: Any = None
        # person_id → enrichment dict. Naturally expires with the
        # pipeline instance (per-session / per-event lifetime).
        self._enrichment_cache: dict[str, dict[str, Any]] = {}

    def wire(self) -> None:
        self._bus.subscribe("stream.unit", self._on_unit)

    async def _on_unit(self, event: Event) -> None:
        try:
            payload = event.payload
            entities = payload.get("entities", [])
            if not entities:
                return

            unit_id = payload.get("id", "")
            thread_id = payload.get("thread_id", "")

            for name in entities:
                if not isinstance(name, str) or not name.strip():
                    continue
                await self._resolve_one(name.strip(), unit_id, thread_id)
        except Exception:
            logger.exception("EntityResolver: failed processing stream.unit")

    async def _resolve_one(
        self, name: str, unit_id: str, thread_id: str
    ) -> None:
        entity_type = "unknown"
        entity_id = None
        metadata: dict = {}

        # Try People ontology lookup
        if self._people is not None:
            try:
                results = self._people.search(name, limit=1)
                if results and results[0].score >= 0.6:
                    hit = results[0]
                    entity_type = "person"
                    entity_id = hit.object_id
                    metadata = {"name": hit.title, "score": hit.score}
            except Exception:
                logger.debug(
                    "EntityResolver: people lookup failed for %r", name
                )

        # Enrich resolved persons with tier / tags / recency from
        # the People Intelligence subsystem. Best-effort — a failure
        # here must not interfere with the entity write or emit.
        enrichment: dict[str, Any] = {}
        if entity_type == "person" and entity_id:
            enrichment = self._enrich_person(entity_id)

        # Write to context store
        if self._context is not None:
            try:
                entity_record = {
                    "name": name,
                    "type": entity_type,
                    "entity_id": entity_id,
                }
                if enrichment:
                    entity_record.update(enrichment)
                self._context.add_entity(entity_record)
            except Exception:
                logger.debug(
                    "EntityResolver: context store write failed for %r", name
                )

        # Emit resolved event
        await self._bus.emit(Event(
            event_type="entity.resolved",
            timestamp=datetime.now(),
            source="entity_resolver",
            payload={
                "name": name,
                "type": entity_type,
                "entity_id": entity_id,
                "metadata": metadata,
                "unit_id": unit_id,
                "thread_id": thread_id,
            },
        ))

    # ── Enrichment ───────────────────────────────────────────────────

    def _enrich_person(self, person_id: str) -> dict[str, Any]:
        """Fetch classification + profile summary for a resolved person.

        Returns a dict with keys: tier, context_tags, days_since_last,
        channels_active. Any missing/failed piece comes back as the
        neutral default (None or []). The full call is wrapped in a
        try/except — the companion chat path must never crash here.

        Results are cached per person_id for the lifetime of this
        pipeline instance so the same name mentioned twice in a
        session is essentially free the second time.
        """
        if not _intel_available:
            return {}

        cached = self._enrichment_cache.get(person_id)
        if cached is not None:
            return cached

        enrichment: dict[str, Any] = {}
        try:
            if self._classifier_runner is None:
                self._classifier_runner = ClassifierRunner()
            if self._profile_builder is None:
                self._profile_builder = ProfileBuilder()

            classification = None
            profile = None
            try:
                classification = self._classifier_runner.get_classification(
                    person_id
                )
            except Exception:
                logger.debug(
                    "EntityResolver: get_classification failed for %r",
                    person_id,
                )
            try:
                profile = self._profile_builder.build(person_id)
            except Exception:
                logger.debug(
                    "EntityResolver: profile build failed for %r",
                    person_id,
                )

            tier_value = None
            context_tags: list[str] = []
            if classification is not None:
                try:
                    tier_value = (
                        classification.tier.value
                        if classification.tier is not None
                        else None
                    )
                except Exception:
                    tier_value = None
                try:
                    raw_tags = classification.context_tags or []
                    context_tags = [
                        t["tag"] for t in raw_tags[:3] if isinstance(t, dict) and "tag" in t
                    ]
                except Exception:
                    context_tags = []

            days_since_last = None
            channels_active: list[str] = []
            if profile is not None:
                try:
                    days_since_last = profile.days_since_last
                except Exception:
                    days_since_last = None
                try:
                    channels_active = list(profile.channels_active or [])[:3]
                except Exception:
                    channels_active = []

            enrichment = {
                "tier": tier_value,
                "context_tags": context_tags,
                "days_since_last": days_since_last,
                "channels_active": channels_active,
            }
        except Exception:
            logger.debug(
                "EntityResolver: enrichment failed for %r", person_id
            )
            enrichment = {}

        self._enrichment_cache[person_id] = enrichment
        return enrichment
