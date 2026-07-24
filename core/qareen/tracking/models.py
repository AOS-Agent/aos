"""Tracking data model — Shipment, TrackingEvent, Milestone.

Kept deliberately focused for auto-tracker#1: orders/order items, linked
numbers, and detection priors arrive with the migration task
(auto-tracker#2). Field names mirror the planned `shipments` /
`shipment_events` tables so the ORM-free mapping later is mechanical.

Compatible with system Python 3.9: dataclasses + typing, no `|` unions at
runtime, no match statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Milestone(str, Enum):
    """Canonical shipment milestones.

    Every carrier maps its own status codes into these via the pack's
    ``status_map``; the dashboard and downstream consumers never see
    carrier-specific codes. Order matters for the happy path:
    label_created → picked_up → in_transit → out_for_delivery → delivered.
    The remaining four are off-path states.
    """

    LABEL_CREATED = "label_created"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    FAILED_ATTEMPT = "failed_attempt"
    RETURNED = "returned"
    EXPIRED = "expired"

    @classmethod
    def is_terminal(cls, milestone: "Milestone") -> bool:
        """Terminal states stop polling (delivered → stop + auto-archive)."""
        return milestone in (cls.DELIVERED, cls.RETURNED, cls.EXPIRED)


# Happy-path ordering, used by the scheduler (later task) for monotonicity
# checks and by the dashboard for kanban column order.
HAPPY_PATH: List[Milestone] = [
    Milestone.LABEL_CREATED,
    Milestone.PICKED_UP,
    Milestone.IN_TRANSIT,
    Milestone.OUT_FOR_DELIVERY,
    Milestone.DELIVERED,
]


@dataclass
class Shipment:
    """One tracked shipment, keyed on the canonical tracking number.

    `tracking_number` is ALWAYS canonical (see engine.canonicalize): spaces
    and hyphens stripped, uppercased. The same number arriving via multiple
    sources produces one Shipment with multiple source links (dedup rule).
    """

    tracking_number: str
    carrier: str  # pack slug, e.g. "ups" — directory name under carriers/
    milestone: Milestone = Milestone.LABEL_CREATED
    direction: str = "inbound"  # inbound | outbound | return
    status: str = "active"  # active | delivered | expired | archived
    source: str = "manual"  # api | email | manual | digest
    eta: Optional[datetime] = None
    merchant: Optional[str] = None
    merchant_domain: Optional[str] = None
    label: Optional[str] = None
    confidence: float = 1.0  # detection confidence; 1.0 for manual adds
    first_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: Optional[str] = None


@dataclass
class TrackingEvent:
    """One carrier scan, normalized.

    Append-only in storage: the carrier API is a source, never the store
    (DHL purges server-side history). Ordering uses carrier `seq` +
    `fetched_at`, never raw event timestamps alone (clock skew).

    `milestone` is None when the carrier code isn't in the pack's
    status_map — the raw event is still stored (with `carrier_code`) so a
    pack update can re-map history later; we never guess a milestone.
    """

    milestone: Optional[Milestone]
    description: str = ""
    timestamp: Optional[datetime] = None  # carrier-reported event time
    fetched_at: Optional[datetime] = None  # when we pulled it from the API
    location: Optional[str] = None
    seq: int = 0  # carrier sequence when available, else poll order
    carrier_code: Optional[str] = None  # raw carrier status code, pre-mapping
    raw: Dict[str, Any] = field(default_factory=dict)  # unmodified event JSON
