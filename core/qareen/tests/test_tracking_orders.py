"""Tests for order extraction + backfill (auto-tracker#21).

Covers: defensive payload validation (schema-valid + garbage paths), the
backfill sweep over a tmp comms.db with an INJECTED fake extractor (the
real `claude` CLI is never spawned), privacy exclusion, dry-run vs --write
persistence through a fake duck-typed store, and shipment linking.

All DBs live under tmp_path — the real ~/.aos/data/*.db is never touched.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import orders  # noqa: E402
from qareen.tracking.orders import (  # noqa: E402
    OrderExtraction,
    is_order_confirmation,
    parse_order_payload,
    render_report,
    run_backfill,
    sender_domain,
)

# -- fixtures -----------------------------------------------------------------

def _make_comms_db(path: Path, messages) -> Path:
    """Build a minimal comms.db messages table with the given rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages ("
        " id INTEGER PRIMARY KEY, channel TEXT, direction TEXT,"
        " sender_id TEXT, content TEXT, timestamp TEXT,"
        " person_id TEXT, conversation_id TEXT)"
    )
    for msg in messages:
        conn.execute(
            "INSERT INTO messages (id, channel, direction, sender_id, content,"
            " timestamp, person_id, conversation_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                msg["id"],
                msg.get("channel", "email"),
                msg.get("direction", "in"),
                msg.get("sender_id"),
                msg.get("content"),
                msg.get("timestamp", "2024-01-05T10:00:00"),
                msg.get("person_id"),
                msg.get("conversation_id"),
            ),
        )
    conn.commit()
    conn.close()
    return path


ORDER_EMAIL = (
    "From: orders@digikey.example.com\n"
    "Thank you for your order! Order confirmation #75123456\n"
    "Order date: 2024-01-04\n"
    "1x Oscilloscope Probe Kit — $89.00 (SKU: PROBE-1)\n"
    "2x USB-C Cable — $12.50\n"
    "Total: $114.00 USD"
)

NOT_ORDER_EMAIL = "Hey, are we still on for lunch tomorrow? - Sam"

# A second order-confirmation-shaped email from a different merchant, so
# merchant-keyed fake extractors only fire on the digikey message.
RESTRICTED_EMAIL = (
    "RestrictedShop order confirmation #R9988\n"
    "1x Mystery Box — $5.00\nTotal: $5.00 USD"
)


@pytest.fixture
def comms_db(tmp_path):
    return _make_comms_db(tmp_path / "comms.db", [
        {"id": 1, "sender_id": "orders@digikey.example.com", "content": ORDER_EMAIL},
        {"id": 2, "sender_id": "sam@example.com", "content": NOT_ORDER_EMAIL},
        # outbound copy of the same email — must not be scanned
        {"id": 3, "sender_id": "me@example.com", "content": ORDER_EMAIL,
         "direction": "out"},
        # privacy-restricted sender
        {"id": 4, "sender_id": "restricted@shop.example.com", "content": RESTRICTED_EMAIL},
    ])


VALID_PAYLOAD = {
    "order": {
        "merchant": "DigiKey",
        "merchant_domain": "digikey.example.com",
        "order_number": "75123456",
        "order_date": "2024-01-04",
        "total": 114.00,
        "currency": "USD",
        "items": [
            {"name": "Oscilloscope Probe Kit", "qty": 1, "price": 89.00, "sku": "PROBE-1"},
            {"name": "USB-C Cable", "qty": 2, "price": 12.50, "sku": None},
        ],
    }
}


class FakeStore:
    """Duck-typed stand-in for ShipmentStore (orders subset)."""

    def __init__(self):
        self.orders = []  # upsert_order kwargs, in call order
        self.links = []  # (shipment_id, order_id)

    def upsert_order(self, **kwargs):
        self.orders.append(kwargs)
        return "ord_fake%02d" % len(self.orders)

    def link_shipment_order(self, shipment_id, order_id):
        self.links.append((shipment_id, order_id))


# -- parse_order_payload --------------------------------------------------------


def test_parse_valid_payload():
    ex = parse_order_payload(VALID_PAYLOAD)
    assert isinstance(ex, OrderExtraction)
    assert ex.order_number == "75123456"
    assert ex.merchant == "DigiKey"
    assert ex.total == 114.00
    assert len(ex.items) == 2
    assert ex.items[1].qty == 2
    assert ex.items[0].sku == "PROBE-1"


@pytest.mark.parametrize("garbage", [
    None,
    "not json at all",
    42,
    [],
    {},
    {"order": None},
    {"order": "a string"},
    {"order": {}},  # no order_number
    {"order": {"order_number": ""}},
    {"order": {"order_number": None}},
    {"order": {"order_number": "   "}},  # whitespace-only
    {"entities": []},  # wrong schema entirely
])
def test_parse_garbage_returns_none(garbage):
    assert parse_order_payload(garbage) is None


def test_parse_tolerates_bad_items_and_scalars():
    payload = {
        "order": {
            "order_number": " A123 ",  # whitespace stripped
            "total": "$1,234.56",  # currency-string tolerated
            "currency": 7,  # nonsense → "7" is harmless; but None-ish types drop
            "items": [
                {"name": "Good", "qty": "3", "price": "9.99"},
                {"name": ""},  # dropped: no name
                "junk",  # dropped: not a dict
                {"name": "NoQty"},  # qty defaults to 1
                {"name": "ZeroQty", "qty": 0},  # clamped to 1
            ],
        }
    }
    ex = parse_order_payload(payload)
    assert ex is not None
    assert ex.order_number == "A123"
    assert ex.total == 1234.56
    assert [i.name for i in ex.items] == ["Good", "NoQty", "ZeroQty"]
    assert ex.items[0].qty == 3
    assert ex.items[0].price == 9.99
    assert ex.items[1].qty == 1
    assert ex.items[2].qty == 1


def test_parse_default_domain_fallback():
    payload = {"order": {"order_number": "X1", "merchant_domain": None}}
    ex = parse_order_payload(payload, default_domain="shop.example.com")
    assert ex.merchant_domain == "shop.example.com"
    # explicit domain wins over the fallback
    payload["order"]["merchant_domain"] = "real.com"
    ex = parse_order_payload(payload, default_domain="shop.example.com")
    assert ex.merchant_domain == "real.com"


def test_sender_domain():
    assert sender_domain("orders@digikey.example.com") == "digikey.example.com"
    assert sender_domain("Jane <jane@shop.example.net>") == "shop.example.net"
    assert sender_domain("not-an-address") is None
    assert sender_domain(None) is None


def test_is_order_confirmation():
    assert is_order_confirmation(ORDER_EMAIL)
    assert is_order_confirmation("Your receipt from ACME")
    assert not is_order_confirmation(NOT_ORDER_EMAIL)
    assert not is_order_confirmation("")


# -- run_backfill ---------------------------------------------------------------

def fake_extractor_factory(payloads_by_snippet):
    """Build a fake extractor keyed on a content substring."""

    def extract(text):
        for snippet, payload in payloads_by_snippet.items():
            if snippet in text:
                return payload
        return {"order": None}

    return extract


def test_backfill_dry_run_writes_nothing(comms_db):
    store = FakeStore()
    extractor = fake_extractor_factory({"digikey.example.com": VALID_PAYLOAD})
    report = run_backfill(comms_db, store=store, extractor=extractor, write=False)

    assert report["scanned"] == 2  # both order emails; outbound + lunch skipped
    assert report["extracted"] >= 1
    assert store.orders == []  # dry run: nothing persisted
    assert store.links == []


def test_backfill_write_persists_and_links(comms_db):
    store = FakeStore()
    extractor = fake_extractor_factory({"digikey.example.com": VALID_PAYLOAD})
    def link_lookup(domain):
        return ["shp_1", "shp_2"] if domain == "digikey.example.com" else []
    report = run_backfill(
        comms_db,
        store=store,
        extractor=extractor,
        link_lookup=link_lookup,
        write=True,
    )

    assert report["orders_written"] == 1
    assert len(store.orders) == 1
    order = store.orders[0]
    assert order["order_number"] == "75123456"
    assert order["merchant_domain"] == "digikey.example.com"
    assert order["total"] == 114.00
    assert [i["name"] for i in order["items"]] == [
        "Oscilloscope Probe Kit", "USB-C Cable",
    ]
    # N:M links recorded for both active shipments of the merchant
    assert report["links_written"] == 2
    assert store.links == [("shp_1", "ord_fake01"), ("shp_2", "ord_fake01")]


def test_backfill_privacy_excluded(comms_db, monkeypatch):
    # restricted@shop.example.com is privacy_level 3 → excluded before extraction
    real_lookup = orders.sender_privacy_level

    def fake_privacy(sender, people_db_path=None):
        if sender == "restricted@shop.example.com":
            return 3
        return 0

    monkeypatch.setattr(orders, "sender_privacy_level", fake_privacy)
    calls = []

    def counting_extractor(text):
        calls.append(text)
        return VALID_PAYLOAD

    report = run_backfill(comms_db, extractor=counting_extractor, privacy_min_level=2)
    assert report["skipped_privacy"] == 1
    # extractor was never invoked for the restricted sender's message
    assert all("restricted" not in c for c in calls)
    monkeypatch.setattr(orders, "sender_privacy_level", real_lookup)


def test_backfill_no_order_and_error_paths(comms_db):
    def flaky_extractor(text):
        if "digikey" in text:
            return {"garbage": True}  # parseable but unusable → no_order
        raise RuntimeError("boom")  # restricted sender email → error

    report = run_backfill(comms_db, extractor=flaky_extractor, privacy_min_level=99)
    assert report["no_order"] == 1
    assert report["errors"] == 1
    assert report["extracted"] == 0


def test_backfill_prefilter_drops_non_orders(comms_db):
    def extractor(text):
        raise AssertionError("extractor must not run on non-order mail")

    run_backfill(comms_db, extractor=extractor, limit=1)  # only msg id 4 (order email)
    # scanning everything with a sentinel extractor would blow up on msg 2;
    # here we assert the regex gate via a full run with a counting fake
    seen = []
    run_backfill(comms_db, extractor=lambda t: seen.append(t) or {"order": None},
                 privacy_min_level=99)
    assert all(is_order_confirmation(t) for t in seen)


def test_render_report_smoke(comms_db):
    extractor = fake_extractor_factory({"digikey.example.com": VALID_PAYLOAD})
    report = run_backfill(comms_db, extractor=extractor)
    text = render_report(report)
    assert "DRY-RUN" in text
    assert "dry run" in text


# -- main() CLI -----------------------------------------------------------------

def test_main_dry_run(comms_db, monkeypatch, capsys):
    monkeypatch.setattr(
        orders, "claude_extractor",
        fake_extractor_factory({"digikey.example.com": VALID_PAYLOAD}),
    )
    rc = orders.main(["--backfill", "--comms-db", str(comms_db), "--limit", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


def test_main_requires_backfill_flag(capsys):
    assert orders.main([]) == 1


def test_main_missing_comms_db(tmp_path, capsys):
    rc = orders.main(["--backfill", "--comms-db", str(tmp_path / "nope.db")])
    assert rc == 1
