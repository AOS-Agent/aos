"""Tests for the Shipment ontology surface (auto-tracker#23-ontology):

- types.py additions: ObjectType.SHIPMENT/ORDER, LinkType.PART_OF, the
  ontology Shipment dataclass.
- ShipmentAdapter get/list/count/search/update against a tmp ShipmentStore.
- Derived links: about→person (person_id), received_via→message (event
  raw message_id), part_of→order (order_shipments), plus explicit
  create_link/get_links via the links table, and clean degradation when
  store data is absent.

All DBs live under tmp_path — the real ~/.aos/data/*.db is never touched.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.ontology.adapters.shipment import ShipmentAdapter  # noqa: E402
from qareen.ontology.types import (  # noqa: E402
    LinkType,
    ObjectType,
)
from qareen.ontology.types import (
    Shipment as OntologyShipment,
)
from qareen.tracking.models import Milestone, Shipment, TrackingEvent  # noqa: E402
from qareen.tracking.store import ShipmentStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return ShipmentStore(tmp_path / "qareen.db")


@pytest.fixture
def adapter(store):
    return ShipmentAdapter(store)


def make_shipment(number="1Z999AA10123456784", carrier="ups", **kw):
    kw.setdefault("direction", "inbound")
    kw.setdefault("source", "manual")
    return Shipment(tracking_number=number, carrier=carrier, **kw)


# -- types.py additions -----------------------------------------------------


def test_object_type_members():
    assert ObjectType.SHIPMENT.value == "shipment"
    assert ObjectType.ORDER.value == "order"
    assert LinkType.PART_OF.value == "part_of"


def test_ontology_shipment_dataclass_defaults():
    obj = OntologyShipment(id="shp_1", tracking_number="1ZABC", carrier="ups")
    assert obj.milestone == "label_created"
    assert obj.status == "active"
    assert obj.person_id is None
    assert obj.confidence == 1.0


# -- get / list / count / search --------------------------------------------


def test_get_returns_ontology_shipment(adapter, store):
    shipment_id, created = store.upsert_shipment(
        make_shipment(merchant="Amazon", merchant_domain="amazon.ca",
                      label="USB cable"),
        category="Shopping",
        person_id="person_1",
    )
    assert created

    obj = adapter.get(shipment_id)
    assert obj is not None
    assert obj.id == shipment_id
    assert obj.tracking_number == "1Z999AA10123456784"
    assert obj.carrier == "ups"
    assert obj.merchant == "Amazon"
    assert obj.merchant_domain == "amazon.ca"
    assert obj.category == "Shopping"
    assert obj.label == "USB cable"
    assert obj.person_id == "person_1"
    assert isinstance(obj.first_seen, datetime)


def test_get_missing_returns_none(adapter):
    assert adapter.get("shp_nope") is None


def test_list_and_count_with_filters(adapter, store):
    store.upsert_shipment(make_shipment("1Z999AA10123456784", carrier="ups"))
    store.upsert_shipment(make_shipment("9400100000000000000001", carrier="usps"))
    fedex_id, _ = store.upsert_shipment(
        make_shipment("962200000000", carrier="fedex")
    )
    store.update_shipment(fedex_id, status="archived")

    assert adapter.count() == 3
    assert adapter.count(filters={"carrier": "ups"}) == 1
    assert adapter.count(filters={"status": "archived"}) == 1

    all_objs = adapter.list()
    assert len(all_objs) == 3
    assert {o.carrier for o in all_objs} == {"ups", "usps", "fedex"}
    assert all(isinstance(o, OntologyShipment) for o in all_objs)

    ups_only = adapter.list(filters={"carrier": "ups"})
    assert len(ups_only) == 1
    assert ups_only[0].tracking_number == "1Z999AA10123456784"

    archived = adapter.list(filters={"status": "archived"})
    assert len(archived) == 1
    assert archived[0].id == fedex_id

    # limit/offset paging
    assert len(adapter.list(limit=2)) == 2
    assert len(adapter.list(limit=2, offset=2)) == 1


def test_search_matches_number_label_merchant(adapter, store):
    store.upsert_shipment(
        make_shipment(merchant="DigiKey", label="oscilloscope probes")
    )
    hits = adapter.search("1Z999")
    assert len(hits) == 1
    assert hits[0].object_type == ObjectType.SHIPMENT

    assert adapter.search("digikey")  # merchant LIKE is case-insensitive in SQLite
    assert adapter.search("probes")
    assert adapter.search("zzz-no-match") == []
    assert adapter.search("") == []


# -- update / create / delete ------------------------------------------------


def test_update_allowlisted_fields(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    obj = adapter.update(shipment_id, {"label": "gift", "status": "archived"})
    assert obj is not None
    assert obj.label == "gift"
    assert obj.status == "archived"


def test_update_ignores_non_allowlisted_fields(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    obj = adapter.update(shipment_id, {"tracking_number": "HACKED"})
    assert obj is not None
    assert obj.tracking_number == "1Z999AA10123456784"


def test_create_not_supported(adapter):
    with pytest.raises(NotImplementedError):
        adapter.create(OntologyShipment(id="x", tracking_number="1", carrier="ups"))


def test_delete_always_false(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    assert adapter.delete(shipment_id) is False
    assert adapter.get(shipment_id) is not None  # still there


# -- derived links ------------------------------------------------------------


def test_about_person_link(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment(), person_id="person_9")
    assert adapter.get_links(shipment_id, ObjectType.PERSON) == ["person_9"]
    assert adapter.get_links(shipment_id, ObjectType.PERSON, LinkType.ABOUT) == ["person_9"]
    # wrong link type → nothing
    assert adapter.get_links(shipment_id, ObjectType.PERSON, LinkType.PART_OF) == []


def test_received_via_message_links(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    store.append_event(shipment_id, TrackingEvent(
        milestone=Milestone.LABEL_CREATED,
        description="from email",
        timestamp=datetime(2024, 1, 1),
        raw={"message_id": 12345},
    ))
    store.append_event(shipment_id, TrackingEvent(
        milestone=Milestone.PICKED_UP,
        description="from another email",
        timestamp=datetime(2024, 1, 2),
        raw={"message_id": 12399},
    ))
    store.append_event(shipment_id, TrackingEvent(
        milestone=Milestone.IN_TRANSIT,
        description="api poll, no message",
        timestamp=datetime(2024, 1, 3),
        raw={},
    ))
    links = adapter.get_links(shipment_id, ObjectType.MESSAGE, LinkType.RECEIVED_VIA)
    assert links == ["12345", "12399"]


def test_part_of_order_links(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    order_id = store.upsert_order(
        order_number="111-2222222-3333333",
        merchant="Amazon",
        merchant_domain="amazon.ca",
        items=[{"name": "Cable", "qty": 1, "price": 9.99, "sku": None}],
    )
    store.link_shipment_order(shipment_id, order_id)
    assert adapter.get_links(shipment_id, ObjectType.ORDER, LinkType.PART_OF) == [order_id]


def test_links_degrade_when_absent(adapter, store):
    shipment_id, _ = store.upsert_shipment(make_shipment())
    # no person, no events, no orders, no links table rows
    assert adapter.get_links(shipment_id, ObjectType.PERSON) == []
    assert adapter.get_links(shipment_id, ObjectType.MESSAGE) == []
    assert adapter.get_links(shipment_id, ObjectType.ORDER) == []


def test_create_link_and_get_links_roundtrip(adapter, store):
    # Explicit links need the links table — create it the way the runtime
    # schema does (the tracking SCHEMA_SQL doesn't own it).
    conn = sqlite3.connect(str(store.db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS links ("
        " id TEXT PRIMARY KEY, link_type TEXT, from_type TEXT, from_id TEXT,"
        " to_type TEXT, to_id TEXT, direction TEXT, properties TEXT,"
        " created_at TEXT, created_by TEXT)"
    )
    conn.commit()
    conn.close()

    shipment_id, _ = store.upsert_shipment(make_shipment())
    link = adapter.create_link(
        shipment_id, ObjectType.NOTE, "knowledge/orders/amazon.md", LinkType.REFERENCES
    )
    assert link.source_type == ObjectType.SHIPMENT
    assert link.target_id == "knowledge/orders/amazon.md"

    assert adapter.get_links(shipment_id, ObjectType.NOTE, LinkType.REFERENCES) == [
        "knowledge/orders/amazon.md"
    ]


def test_adapter_degrades_with_storeless_stub(tmp_path):
    """A store without the expected methods must not raise — just fewer links."""

    class StubStore:
        db_path = None

    stub_adapter = ShipmentAdapter(StubStore())
    assert stub_adapter.get("anything") is None
    assert stub_adapter.list() == []
    assert stub_adapter.count() == 0
    assert stub_adapter.get_links("anything", ObjectType.PERSON) == []
    assert stub_adapter.search("x") == []
