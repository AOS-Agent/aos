"""Tests for core/qareen/tracking/chitchats.py (auto-tracker#7).

HTTP is faked with an injected transport callable; credentials are faked by
patching ``get_credentials`` / the secret reader. Fixtures are modeled on
the Shipment shape in ~/project/chitchats-mcp/src/tools/shipments.ts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from qareen.tracking import chitchats
from qareen.tracking.chitchats import (
    ChitChatsClient,
    ChitChatsError,
    ChitChatsRateLimited,
    ChitChatsSync,
    map_shipment,
    map_status,
)
from qareen.tracking.models import Milestone

# --- Fixtures modeled on the real API response --------------------------------

def _cc_shipment(**overrides):
    raw = {
        "id": "ABCD1234",
        "status": "in_transit",
        "order_id": "NU-1042",
        "order_store": "shopify",
        "to_name": "Jane Doe",
        "to_city": "Austin",
        "to_province_code": "TX",
        "to_country_code": "US",
        "postage_type": "usps_priority",
        "carrier": "usps",
        "carrier_tracking_code": "9400 1000 0000 0000 0000 12",
        "tracking_url": "https://chitchats.com/tracking/ABCD1234",
        "ship_date": "2026-07-20T14:03:00.000Z",
        "created_at": "2026-07-19T09:31:00.000Z",
    }
    raw.update(overrides)
    return raw


class FakeTransport:
    """Records calls and replays scripted (status, payload) responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        if not self.responses:
            raise AssertionError("unexpected extra request: %s" % url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStore:
    """Duck-typed store per the ChitChatsSync protocol docstring."""

    def __init__(self):
        self.shipments = {}  # (carrier, number) -> (id, Shipment)
        self.numbers = []  # (shipment_id, carrier, number, role)
        self.events = []  # (shipment_id, TrackingEvent)
        self.state = {}
        self._seq = 0

    def upsert_shipment(self, shipment):
        """Mirrors the real store: returns (shipment_id, created)."""
        key = (shipment.carrier, shipment.tracking_number)
        created = key not in self.shipments
        if created:
            self._seq += 1
            self.shipments[key] = ("shp-%d" % self._seq, shipment)
        else:
            sid, existing = self.shipments[key]
            existing.milestone = shipment.milestone  # update in place
        return self.shipments[key][0], created

    def add_number(self, shipment_id, number, carrier=None, role="handoff"):
        entry = (shipment_id, carrier, number, role)
        if entry not in self.numbers:
            self.numbers.append(entry)

    def append_event(self, shipment_id, event):
        self.events.append((shipment_id, event))

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value


def _client_for(payload, transport=None):
    transport = transport or FakeTransport([(200, payload)])
    return ChitChatsClient("client-1", "token-1", transport=transport), transport


# --- Status mapping ------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", Milestone.LABEL_CREATED),
        ("ready", Milestone.LABEL_CREATED),
        ("inducted", Milestone.PICKED_UP),
        ("in_transit", Milestone.IN_TRANSIT),
        ("out_for_delivery", Milestone.OUT_FOR_DELIVERY),
        ("delivered", Milestone.DELIVERED),
        ("exception", Milestone.EXCEPTION),
        ("cancelled", Milestone.EXPIRED),
        ("refunded", Milestone.EXPIRED),
        ("something_new", Milestone.LABEL_CREATED),
        (None, Milestone.LABEL_CREATED),
    ],
)
def test_status_mapping(status, expected):
    assert map_status(status) is expected


# --- Shipment mapping ----------------------------------------------------------

def test_map_shipment_outbound_fields():
    shipment = map_shipment(_cc_shipment())
    assert shipment.carrier == "chitchats"
    assert shipment.direction == "outbound"
    assert shipment.source == "api"
    assert shipment.tracking_number == "ABCD1234"
    assert shipment.milestone is Milestone.IN_TRANSIT
    assert shipment.status == "active"
    assert shipment.merchant == "shopify"
    assert shipment.label == "NU-1042 → Jane Doe, Austin, TX, US"
    assert shipment.first_seen.year == 2026 and shipment.first_seen.month == 7


def test_map_shipment_requires_id():
    with pytest.raises(ValueError):
        map_shipment({"status": "pending"})


def test_map_shipment_delivered_marks_status_delivered():
    shipment = map_shipment(_cc_shipment(status="delivered"))
    assert shipment.milestone is Milestone.DELIVERED
    assert shipment.status == "delivered"


# --- Sync behavior -------------------------------------------------------------

def test_sync_upserts_and_captures_handoff():
    store = FakeStore()
    client, transport = _client_for([_cc_shipment()])
    sync = ChitChatsSync(store, client=client)

    summary = sync.sync()

    assert summary["ok"] is True
    assert summary["synced"] == 1
    assert summary["handoffs"] == 1
    assert ("shp-1", "usps", "9400100000000000000012", "handoff") in store.numbers

    # Handoff note event recorded, alongside the milestone event.
    kinds = [e.carrier_code for _, e in store.events]
    assert "handoff" in kinds
    assert "in_transit" in kinds
    handoff_event = next(e for _, e in store.events if e.carrier_code == "handoff")
    assert handoff_event.milestone is None
    assert "9400100000000000000012" in handoff_event.description

    # Auth header sent raw (no Bearer prefix), per the MCP client.
    _, url, headers = transport.calls[0]
    assert headers["Authorization"] == "token-1"
    assert url.startswith("https://chitchats.com/api/v1/clients/client-1/shipments?")


def test_sync_without_handoff_leaves_numbers_empty():
    store = FakeStore()
    client, _ = _client_for(
        [_cc_shipment(status="ready", carrier=None, carrier_tracking_code=None)]
    )
    summary = ChitChatsSync(store, client=client).sync()
    assert summary["synced"] == 1
    assert summary["handoffs"] == 0
    assert store.numbers == []


def test_resync_is_idempotent():
    store = FakeStore()
    client, _ = _client_for([_cc_shipment()], transport=FakeTransport(
        [(200, [_cc_shipment()]), (200, [_cc_shipment()])]
    ))
    sync = ChitChatsSync(store, client=client)

    first = sync.sync()
    second = sync.sync()

    assert first["synced"] == second["synced"] == 1
    assert len(store.shipments) == 1  # same label updates, never duplicates
    assert len(store.numbers) == 1  # handoff number added once
    # Milestone unchanged on the second pass → no duplicate events.
    first_events = len(store.events)
    assert second["events"] == 0
    assert len(store.events) == first_events


def test_resync_appends_event_when_milestone_advances():
    store = FakeStore()
    transport = FakeTransport(
        [
            (200, [_cc_shipment(status="inducted", carrier=None,
                                carrier_tracking_code=None)]),
            (200, [_cc_shipment(status="in_transit")]),
        ]
    )
    client = ChitChatsClient("client-1", "token-1", transport=transport)
    sync = ChitChatsSync(store, client=client)

    sync.sync()
    sync.sync()

    milestones = [e.milestone for _, e in store.events if e.carrier_code != "handoff"]
    assert milestones == [Milestone.PICKED_UP, Milestone.IN_TRANSIT]
    key = ("chitchats", "ABCD1234")
    assert store.shipments[key][1].milestone is Milestone.IN_TRANSIT


def test_missing_credentials_degrades_gracefully(monkeypatch, caplog):
    monkeypatch.setattr(chitchats, "get_credentials", lambda: None)
    store = FakeStore()
    sync = ChitChatsSync(store)  # no client injected → hits credential path

    with caplog.at_level("WARNING", logger="qareen.tracking.chitchats"):
        summary = sync.sync()

    assert summary == {"ok": False, "reason": "missing_credentials", "synced": 0}
    assert store.shipments == {} and store.events == []
    assert any("credentials" in r.message for r in caplog.records)


def test_missing_secret_value_returns_none(monkeypatch):
    class _Proc:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(chitchats.subprocess, "run", lambda *a, **k: _Proc())
    assert chitchats._read_secret("CHITCHATS_API_KEY") is None
    assert chitchats.get_credentials() is None


def test_checkpoint_drives_incremental_window():
    store = FakeStore()
    store.set_state(chitchats.STATE_LAST_SYNC, "2026-07-10T08:00:00+00:00")
    transport = FakeTransport([(200, [])])
    client = ChitChatsClient("client-1", "token-1", transport=transport)

    summary = ChitChatsSync(store, client=client).sync()

    assert summary["ok"] is True
    _, url, _ = transport.calls[0]
    assert "from_date=2026-07-10" in url
    # Checkpoint advanced after a successful sync.
    assert store.get_state(chitchats.STATE_LAST_SYNC) != "2026-07-10T08:00:00+00:00"


def test_api_error_returns_failure_summary():
    store = FakeStore()
    client, _ = _client_for(None, transport=FakeTransport(
        [(500, {"error": "internal"})]
    ))
    summary = ChitChatsSync(store, client=client).sync()
    assert summary["ok"] is False
    assert summary["reason"] == "api_error"
    assert summary["synced"] == 0
    assert store.shipments == {}


def test_rate_limited_raises_typed_error():
    client, _ = _client_for(None, transport=FakeTransport([(429, None)]))
    with pytest.raises(ChitChatsRateLimited):
        client.list_shipments()


def test_one_bad_row_does_not_sink_batch():
    store = FakeStore()
    client, _ = _client_for([
        _cc_shipment(id=None),  # junk row
        _cc_shipment(id="GOOD1"),
    ])
    summary = ChitChatsSync(store, client=client).sync()
    assert summary["ok"] is True
    assert summary["errors"] == 1
    assert summary["synced"] == 1
    assert ("chitchats", "GOOD1") in store.shipments


def test_pagination_follows_full_pages():
    page1 = [_cc_shipment(id="P1-%d" % i) for i in range(3)]
    page2 = [_cc_shipment(id="P2-0")]
    transport = FakeTransport([(200, page1), (200, page2)])
    client = ChitChatsClient("client-1", "token-1", transport=transport)
    store = FakeStore()

    summary = ChitChatsSync(store, client=client).sync(limit=3)

    assert summary["synced"] == 4
    assert len(transport.calls) == 2
    assert "page=2" in transport.calls[1][1]


def test_client_rejects_non_list_payload():
    client, _ = _client_for(None, transport=FakeTransport([(200, {"oops": 1})]))
    with pytest.raises(ChitChatsError):
        client.list_shipments()


# --- Integration: real ShipmentStore (seam regression) -----------------------


def test_sync_against_real_store(tmp_path):
    """ChitChatsSync against the REAL ShipmentStore — regression for the
    (id, created) tuple being treated as a str (sqlite binding error live)
    and the add_number argument-order swap."""
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    client, _transport = _client_for([_cc_shipment()])
    sync = ChitChatsSync(store, client=client)

    summary = sync.sync()

    assert summary["ok"] is True
    assert summary["synced"] == 1
    assert summary["handoffs"] == 1
    assert summary["errors"] == 0

    rows = store.list_shipments(status="active")
    assert len(rows) == 1
    row = rows[0]
    assert row["carrier"] == "chitchats"
    assert row["direction"] == "outbound"
    assert row["milestone"] == "in_transit"

    numbers = store.numbers_for(row["id"])
    assert any(
        n["carrier"] == "usps"
        and n["number"] == "9400100000000000000012"
        and n["role"] == "handoff"
        for n in numbers
    ), numbers

    # Re-sync is idempotent: one shipment, milestone + handoff events once.
    client2, _ = _client_for([_cc_shipment()])
    summary2 = ChitChatsSync(store, client=client2).sync()
    assert summary2["synced"] == 1
    assert summary2["events"] == 0
    assert len(store.list_shipments()) == 1
    assert len(store.events_for(row["id"])) == 2
