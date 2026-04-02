"""Qareen Ontology — Core Interface.

The Ontology is the semantic layer over AOS's heterogeneous storage.
It provides typed access to all objects, relationship traversal,
governed mutations (actions), and unified search.

Usage:
    ontology = Ontology(config)
    person = ontology.get(ObjectType.PERSON, "zeeshan")
    tasks = ontology.linked(person, ObjectType.TASK)
    context = ontology.context_card(ObjectType.PERSON, "zeeshan")
    result = ontology.act("create_task", title="Review pricing", project="nuchay")
"""

from __future__ import annotations

from typing import Any, Callable

from .types import (
    ActionResult, ContextCard, Link, LinkType, ObjectType,
    Person, Task, Project, Goal, Message, Note, Decision,
    Session, Agent, Channel, Integration, Operator, TrustEntry,
)
# Adapter base is imported by concrete adapters, not needed here at runtime.
# We reference it only for type hints.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .adapters.base import Adapter


# Type alias for any ontology object
OntologyObject = Person | Task | Project | Goal | Message | Note | Decision | Session | Agent | Channel | Integration


class Ontology:
    """The semantic layer over AOS data.

    Thin by design. Specific types, specific adapters, hardcoded.
    Not a framework. Not a generic ORM. If this class exceeds
    800 lines including all adapter wiring, it's over-engineered.
    """

    def __init__(self, config_dir: str, data_dir: str, vault_dir: str) -> None:
        """Initialize with paths to AOS directories.

        Args:
            config_dir: ~/.aos/config/
            data_dir: ~/.aos/data/ (SQLite databases)
            vault_dir: ~/vault/
        """
        self._adapters: dict[ObjectType, Any] = {}  # Adapter instances
        self._action_handlers: dict[str, Callable] = {}
        self._event_bus = None  # Set via wire_events()
        self._config_dir = config_dir
        self._data_dir = data_dir
        self._vault_dir = vault_dir

    def wire_events(self, event_bus) -> None:
        """Connect the event bus so actions can emit events."""
        from qareen.events.bus import EventBus
        self._event_bus: EventBus = event_bus

    def register_adapter(self, object_type: ObjectType, adapter: Any) -> None:
        """Register a storage adapter for an object type."""
        self._adapters[object_type] = adapter

    def register_action(self, name: str, handler: Callable) -> None:
        """Register a governed action handler."""
        self._action_handlers[name] = handler

    # -- Read operations --------------------------------------------------

    def get(self, object_type: ObjectType, object_id: str) -> OntologyObject | None:
        """Get a single object by type and id.

        Returns None if not found. Never raises for missing objects.
        """
        adapter = self._adapters.get(object_type)
        if not adapter:
            return None
        return adapter.get(object_id)

    def list(
        self,
        object_type: ObjectType,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OntologyObject]:
        """List objects of a type with optional filters.

        Filters are field-name → value pairs, adapter-interpreted.
        """
        adapter = self._adapters.get(object_type)
        if not adapter:
            return []
        # Inject object type hint so multi-type adapters (WorkAdapter) know
        # which type was requested (task vs project vs goal)
        effective_filters = dict(filters) if filters else {}
        effective_filters.setdefault("_type", object_type.value)
        return adapter.list(filters=effective_filters, limit=limit, offset=offset)

    def count(self, object_type: ObjectType, *, filters: dict[str, Any] | None = None) -> int:
        """Count objects matching filters."""
        adapter = self._adapters.get(object_type)
        if not adapter:
            return 0
        effective_filters = dict(filters) if filters else {}
        effective_filters.setdefault("_type", object_type.value)
        return adapter.count(filters=effective_filters)

    # -- Relationship traversal -------------------------------------------

    def linked(
        self,
        obj: OntologyObject,
        target_type: ObjectType,
        *,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[OntologyObject]:
        """Get objects linked to the given object.

        Args:
            obj: The source object
            target_type: The type of linked objects to return
            link_type: Optional filter by relationship type
            limit: Max results

        Returns:
            List of linked objects of the target type.
        """
        source_type = self._object_type_of(obj)
        adapter = self._adapters.get(source_type)
        if not adapter:
            return []
        linked_ids = adapter.get_links(
            obj_id=self._id_of(obj),
            target_type=target_type,
            link_type=link_type,
            limit=limit,
        )
        target_adapter = self._adapters.get(target_type)
        if not target_adapter:
            return []
        return [target_adapter.get(lid) for lid in linked_ids if target_adapter.get(lid) is not None]

    def link(
        self,
        source: OntologyObject,
        target: OntologyObject,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Create a relationship between two objects."""
        source_type = self._object_type_of(source)
        adapter = self._adapters.get(source_type)
        if not adapter:
            raise ValueError(f"No adapter for {source_type}")
        return adapter.create_link(
            source_id=self._id_of(source),
            target_type=self._object_type_of(target),
            target_id=self._id_of(target),
            link_type=link_type,
            metadata=metadata or {},
        )

    # -- Context cards (pre-built, fast surfacing) -------------------------

    def context_card(self, object_type: ObjectType, object_id: str) -> ContextCard | None:
        """Get the pre-built context card for an entity.

        Returns a cached summary built by overnight processing.
        If no card exists, returns None (caller can fall back to
        live assembly, which is slower).
        """
        adapter = self._adapters.get(object_type)
        if not adapter:
            return None
        return adapter.get_context_card(object_id)

    # -- Search (unified across all types) ---------------------------------

    def search(
        self,
        query: str,
        *,
        types: list[ObjectType] | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search across all (or specified) object types.

        Uses QMD for vault search, SQL for structured data,
        and merges results by relevance.
        """
        results: list[SearchResult] = []
        search_types = types or list(self._adapters.keys())
        # Deduplicate adapters (work adapter registered for TASK, PROJECT, GOAL)
        seen_adapters: set[int] = set()
        for obj_type in search_types:
            adapter = self._adapters.get(obj_type)
            if adapter and id(adapter) not in seen_adapters:
                seen_adapters.add(id(adapter))
                try:
                    adapter_results = adapter.search(query, limit=limit)
                    for r in adapter_results:
                        if isinstance(r, SearchResult):
                            results.append(r)
                        elif hasattr(r, 'id') and hasattr(r, 'title'):
                            # Adapter returned a raw object — wrap it
                            results.append(SearchResult(
                                object_type=obj_type,
                                object_id=r.id,
                                title=getattr(r, 'title', str(r.id)),
                                snippet=getattr(r, 'description', '') or '',
                                score=0.5,
                                obj=r,
                            ))
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("Search failed for %s: %s", obj_type, e)
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # -- Governed mutations (actions) --------------------------------------

    def act(self, action_name: str, **params: Any) -> ActionResult:
        """Execute a governed action.

        Every mutation goes through this method. The action system:
        1. Validates parameters
        2. Executes the mutation
        3. Logs to audit trail
        4. Emits an event via the event bus
        5. Returns a typed result

        Raises ValueError if the action is not registered.
        """
        handler = self._action_handlers.get(action_name)
        if not handler:
            return ActionResult(
                success=False,
                action_name=action_name,
                params=params,
                error=f"Unknown action: {action_name}",
            )
        return handler(self, **params)

    # -- Operator ----------------------------------------------------------

    def operator(self) -> Operator:
        """Load the operator profile from config."""
        import yaml
        from pathlib import Path
        config_path = Path(self._config_dir) / "operator.yaml"
        if not config_path.exists():
            return Operator(name="Operator")
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return Operator(
            name=data.get("name", "Operator"),
            timezone=data.get("timezone", "America/Chicago"),
            language=data.get("communication", {}).get("language", "en"),
            agent_name=data.get("agent_name", "chief"),
        )

    # -- Trust -------------------------------------------------------------

    def trust_for(self, agent_id: str, action_type: str) -> TrustEntry | None:
        """Get the trust level for a specific (agent, action_type) pair."""
        # Implementation reads from trust.yaml or trust table
        raise NotImplementedError

    # -- Write operations -------------------------------------------------

    def create(self, object_type: ObjectType, obj: Any) -> Any:
        """Create a new object through the appropriate adapter."""
        adapter = self._adapters.get(object_type)
        if not adapter:
            return None
        return adapter.create(obj)

    def update(self, object_type: ObjectType, object_id: str, fields: dict) -> Any:
        """Update an object's fields through the appropriate adapter."""
        adapter = self._adapters.get(object_type)
        if not adapter:
            return None
        return adapter.update(object_id, fields)

    def delete(self, object_type: ObjectType, object_id: str) -> bool:
        """Delete an object through the appropriate adapter."""
        adapter = self._adapters.get(object_type)
        if not adapter:
            return False
        return adapter.delete(object_id)

    # -- Lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close all adapter connections. Call on shutdown."""
        closed = set()
        for adapter in self._adapters.values():
            aid = id(adapter)
            if aid not in closed:
                closed.add(aid)
                if hasattr(adapter, 'close'):
                    adapter.close()

    # -- Convenience wrappers ---------------------------------------------

    def resolve_channel(self, person_name: str) -> dict | None:
        """Resolve a person's best messaging channel. Convenience wrapper."""
        adapter = self._adapters.get(ObjectType.PERSON)
        if adapter and hasattr(adapter, 'resolve_channel'):
            return adapter.resolve_channel(person_name)
        return None

    def write_handoff(self, task_id: str, **kwargs) -> Any:
        """Write handoff context for a task. Convenience wrapper."""
        adapter = self._adapters.get(ObjectType.TASK)
        if adapter and hasattr(adapter, 'write_handoff'):
            return adapter.write_handoff(task_id, **kwargs)
        return None

    # -- Internal helpers --------------------------------------------------

    def _object_type_of(self, obj: OntologyObject) -> ObjectType:
        """Determine the ObjectType of an ontology object."""
        type_map = {
            Person: ObjectType.PERSON,
            Task: ObjectType.TASK,
            Project: ObjectType.PROJECT,
            Goal: ObjectType.GOAL,
            Message: ObjectType.MESSAGE,
            Note: ObjectType.NOTE,
            Decision: ObjectType.DECISION,
            Session: ObjectType.SESSION,
            Agent: ObjectType.AGENT,
            Channel: ObjectType.CHANNEL,
            Integration: ObjectType.INTEGRATION,
        }
        for cls, otype in type_map.items():
            if isinstance(obj, cls):
                return otype
        raise TypeError(f"Unknown object type: {type(obj)}")

    def _id_of(self, obj: OntologyObject) -> str:
        """Extract the id from any ontology object."""
        return obj.id


# ---------------------------------------------------------------------------
# Search result wrapper
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class SearchResult:
    """A single search result from unified search."""
    object_type: ObjectType
    object_id: str
    title: str
    snippet: str = ""
    score: float = 0.0
    obj: OntologyObject | None = None  # the full object, if loaded
