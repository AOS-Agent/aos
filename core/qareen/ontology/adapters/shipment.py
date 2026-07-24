"""Shipment Adapter — Auto Tracker shipments through the ontology.

Backed by ``qareen.tracking.store.ShipmentStore`` (duck-typed). The store
owns the shipments tables; this adapter is a read/query surface plus the
ontology link derivation:

- ``shipment → about → person``        from the shipments.person_id column
- ``shipment → received_via → message`` from message ids embedded in stored
                                        event raw_json (email-channel events
                                        carry ``raw["message_id"]``)
- ``shipment → part_of → order``        from the order_shipments N:M table

Derived links are computed on demand — there is nothing to sync. Explicit
links created via ``create_link`` go to the qareen.db ``links`` table (same
table the other adapters use) and are merged into ``get_links`` results.

Every store/SQL access is wrapped: a missing person, message-less shipment,
or absent links table degrades to fewer links, never an exception.

The store is duck-typed (get_shipment_row, events_for, orders_for_shipment,
update_shipment) so tests can substitute fakes; when no store is given a
real ShipmentStore over the default qareen.db is opened lazily.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..model import SearchResult
from ..types import (
    Link,
    LinkType,
    ObjectType,
    Shipment,
)
from .base import Adapter

logger = logging.getLogger(__name__)

# Fields the ontology may mutate on a shipment. This is a subset of the
# store's own update allowlist — identity and lifecycle (milestone, eta,
# numbers) belong to the tracking pipeline, not the ontology.
_UPDATABLE_FIELDS = frozenset({"label", "category", "status", "person_id", "direction"})

# List filters supported by list()/count(): exact-match shipments columns.
_LIST_FILTERS = ("status", "milestone", "carrier", "category", "merchant_domain")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _row_to_shipment(row: dict[str, Any]) -> Shipment:
    return Shipment(
        id=row["id"],
        tracking_number=row["tracking_number"],
        carrier=row["carrier"],
        milestone=row.get("milestone") or "label_created",
        status=row.get("status") or "active",
        direction=row.get("direction") or "inbound",
        eta=_parse_dt(row.get("eta")),
        merchant=row.get("merchant"),
        merchant_domain=row.get("merchant_domain"),
        category=row.get("category"),
        label=row.get("label"),
        person_id=row.get("person_id"),
        source=row.get("source") or "manual",
        confidence=float(row.get("confidence") or 0.0),
        first_seen=_parse_dt(row.get("first_seen")),
        created_at=_parse_dt(row.get("created")),
        updated_at=_parse_dt(row.get("updated")),
    )


class ShipmentAdapter(Adapter):
    """Adapter for SHIPMENT objects, backed by the tracking store."""

    def __init__(self, store: Any = None, db_path: Any = None) -> None:
        if store is None:
            from qareen.tracking.store import ShipmentStore

            store = ShipmentStore(db_path)
        self._store = store
        # Direct SQL (list/count/search, links table) keys off the store's
        # own db_path when it exposes one; an explicit db_path wins.
        self._db_path = db_path or getattr(store, "db_path", None)

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.SHIPMENT

    # -- DB helper (links table + list/count/search) ----------------------

    def _db(self) -> sqlite3.Connection | None:
        if not self._db_path:
            return None
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.warning("ShipmentAdapter: cannot open db: %s", e)
            return None

    # -- Adapter interface -------------------------------------------------

    def get(self, object_id: str) -> Shipment | None:
        try:
            row = self._store.get_shipment_row(object_id)
        except Exception as e:
            logger.warning("ShipmentAdapter.get failed: %s", e)
            return None
        return _row_to_shipment(row) if row else None

    def _select(
        self,
        *,
        filters: dict[str, Any] | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        conn = self._db()
        if conn is None:
            return []
        try:
            query = "SELECT * FROM shipments"
            params: list[Any] = []
            clauses = []
            for key in _LIST_FILTERS:
                if filters and filters.get(key) is not None:
                    clauses.append("%s = ?" % key)
                    params.append(filters[key])
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY updated DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning("ShipmentAdapter.list failed: %s", e)
            return []
        finally:
            conn.close()

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Shipment]:
        return [
            _row_to_shipment(r)
            for r in self._select(filters=filters, limit=limit, offset=offset)
        ]

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        conn = self._db()
        if conn is None:
            return 0
        try:
            query = "SELECT COUNT(*) FROM shipments"
            params: list[Any] = []
            clauses = []
            for key in _LIST_FILTERS:
                if filters and filters.get(key) is not None:
                    clauses.append("%s = ?" % key)
                    params.append(filters[key])
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            row = conn.execute(query, params).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as e:
            logger.warning("ShipmentAdapter.count failed: %s", e)
            return 0
        finally:
            conn.close()

    def create(self, obj: Shipment) -> Shipment:
        """Shipments are created by detection/manual add, not the ontology."""
        raise NotImplementedError(
            "Shipments are created by the tracking pipeline (detection, "
            "manual add, scheduler), not via the ontology adapter."
        )

    def update(self, object_id: str, fields: dict[str, Any]) -> Shipment | None:
        allowed = {k: v for k, v in (fields or {}).items() if k in _UPDATABLE_FIELDS}
        if not allowed:
            return self.get(object_id)
        try:
            ok = self._store.update_shipment(object_id, **allowed)
        except Exception as e:
            logger.warning("ShipmentAdapter.update failed: %s", e)
            return None
        return self.get(object_id) if ok else None

    def delete(self, object_id: str) -> bool:
        """Shipments are archived (status), never hard-deleted, via ontology."""
        return False

    # -- Search -----------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Match tracking number, label, or merchant (case-insensitive)."""
        if not query:
            return []
        conn = self._db()
        if conn is None:
            return []
        try:
            needle = "%%%s%%" % query.replace("%", "").replace("_", "")
            rows = conn.execute(
                """
                SELECT * FROM shipments
                WHERE tracking_number LIKE ? OR label LIKE ? OR merchant LIKE ?
                ORDER BY updated DESC LIMIT ?
                """,
                (needle, needle, needle, limit),
            ).fetchall()
        except sqlite3.Error as e:
            logger.warning("ShipmentAdapter.search failed: %s", e)
            return []
        finally:
            conn.close()
        results: list[SearchResult] = []
        for row in rows:
            obj = _row_to_shipment(dict(row))
            title = obj.label or obj.merchant or obj.tracking_number
            snippet = "%s %s — %s" % (obj.carrier, obj.tracking_number, obj.milestone)
            results.append(
                SearchResult(
                    object_type=ObjectType.SHIPMENT,
                    object_id=obj.id,
                    title=title,
                    snippet=snippet[:200],
                    score=1.0 if query.upper() in obj.tracking_number else 0.5,
                    obj=obj,
                )
            )
        return results

    # -- Links -------------------------------------------------------------
    #
    # Derived links come from store data; explicit links (create_link) live
    # in the qareen.db links table like the other adapters. get_links merges
    # both, degrading per-source on any failure.

    def _derived_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None,
    ) -> list[str]:
        out: list[str] = []
        # shipment → about → person (person_id column)
        if target_type == ObjectType.PERSON and link_type in (None, LinkType.ABOUT):
            try:
                row = self._store.get_shipment_row(obj_id)
                if row and row.get("person_id"):
                    out.append(str(row["person_id"]))
            except Exception as e:
                logger.warning("ShipmentAdapter: person link failed: %s", e)
        # shipment → received_via → message (message ids in event raw_json)
        if target_type == ObjectType.MESSAGE and link_type in (None, LinkType.RECEIVED_VIA):
            try:
                for event in self._store.events_for(obj_id):
                    raw = getattr(event, "raw", None) or {}
                    mid = raw.get("message_id")
                    if mid is not None and str(mid) not in out:
                        out.append(str(mid))
            except Exception as e:
                logger.warning("ShipmentAdapter: message links failed: %s", e)
        # shipment → part_of → order (order_shipments N:M)
        if target_type == ObjectType.ORDER and link_type in (None, LinkType.PART_OF):
            try:
                for order in self._store.orders_for_shipment(obj_id):
                    if order.get("id"):
                        out.append(str(order["id"]))
            except Exception as e:
                logger.warning("ShipmentAdapter: order links failed: %s", e)
        return out

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        ids = self._derived_links(obj_id, target_type, link_type)

        conn = self._db()
        if conn is not None:
            try:
                query = (
                    "SELECT to_id FROM links "
                    "WHERE from_type = 'shipment' AND from_id = ? AND to_type = ?"
                )
                params: list[Any] = [obj_id, target_type.value]
                if link_type is not None:
                    query += " AND link_type = ?"
                    params.append(link_type.value)
                query += " LIMIT ?"
                params.append(limit)
                for row in conn.execute(query, params).fetchall():
                    if row["to_id"] not in ids:
                        ids.append(row["to_id"])
            except sqlite3.Error as e:
                # links table may not exist yet — derived links still stand.
                logger.warning("ShipmentAdapter.get_links failed: %s", e)
            finally:
                conn.close()
        return ids[:limit]

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        now = datetime.now().isoformat()
        link_id = str(uuid.uuid4())[:8]
        props = json.dumps(metadata) if metadata else None

        conn = self._db()
        if conn is not None:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO links "
                    "(id, link_type, from_type, from_id, to_type, to_id, "
                    "direction, properties, created_at, created_by) "
                    "VALUES (?, ?, 'shipment', ?, ?, ?, 'directed', ?, ?, 'shipment_adapter')",
                    (
                        link_id,
                        link_type.value,
                        source_id,
                        target_type.value,
                        target_id,
                        props,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.warning("ShipmentAdapter.create_link failed: %s", e)
            finally:
                conn.close()

        return Link(
            link_type=link_type,
            source_type=ObjectType.SHIPMENT,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.now(),
        )
