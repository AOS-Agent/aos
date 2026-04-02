"""Ontology Adapter — Base Interface.

Every object type has an adapter that maps between the ontology's
typed objects and the underlying storage (SQLite, YAML, markdown).

Adapters are specific, not generic. Each adapter knows exactly which
store it reads from and how to translate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# Forward reference to avoid circular import — Ontology is defined in model.py
from typing import TYPE_CHECKING, Any

from ..types import (
    ContextCard,
    Link,
    LinkType,
    ObjectType,
)

if TYPE_CHECKING:
    pass


class Adapter(ABC):
    """Base class for all ontology storage adapters."""

    @property
    @abstractmethod
    def object_type(self) -> ObjectType:
        """The object type this adapter handles."""
        ...

    @abstractmethod
    def get(self, object_id: str) -> Any | None:
        """Get a single object by id. Returns None if not found."""
        ...

    @abstractmethod
    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List objects with optional filters."""
        ...

    @abstractmethod
    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        """Count objects matching filters."""
        ...

    @abstractmethod
    def create(self, obj: Any) -> Any:
        """Create a new object in storage. Returns the created object."""
        ...

    @abstractmethod
    def update(self, object_id: str, fields: dict[str, Any]) -> Any | None:
        """Update fields on an existing object. Returns updated object."""
        ...

    @abstractmethod
    def delete(self, object_id: str) -> bool:
        """Delete an object. Returns True if deleted, False if not found."""
        ...

    # -- Relationship methods ---------------------------------------------

    @abstractmethod
    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get ids of linked objects.

        Returns a list of target object ids that are linked to the
        source object with the given relationship type.
        """
        ...

    @abstractmethod
    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Create a link between this object and another."""
        ...

    # -- Search -----------------------------------------------------------

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[Any]:
        """Search within this object type's storage."""
        ...

    # -- Context card (pre-built, optional) --------------------------------

    def get_context_card(self, object_id: str) -> ContextCard | None:
        """Get the pre-built context card. Override if supported."""
        return None

    def set_context_card(self, object_id: str, card: ContextCard) -> None:
        """Store a pre-built context card. Override if supported."""
        pass
